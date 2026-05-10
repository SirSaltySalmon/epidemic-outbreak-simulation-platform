"""Monte Carlo ensemble orchestrator (FR-3.4).

Samples ``n_simulations`` parameter draws from the inferred posterior
(Normal/Beta approximation when only summary stats are available, or the raw
posterior samples when they were persisted), runs the vectorized SEIR+D ABM,
and aggregates daily percentiles into the existing ``ForecastResponse``
schema. Parallelization uses a ``ThreadPoolExecutor`` because the per-step
matrix-vector products release the GIL.
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Any, Callable, Iterable, Mapping, cast

import numpy as np

from eosp.core.case_statistics import total_cohort_persons, total_death_equivalents
from eosp.core.compute_config import EnsembleConfig
from eosp.core.models import CaseRecord, ForecastPoint, ForecastResponse, InferenceResult
from eosp.services.abm import SeedState, Trajectory, simulate_trajectory
from eosp.services.geo_buckets import (
    GEO_BUCKET_METRIC_DETAIL,
    GEO_BUCKET_METRIC_ID,
    GEO_BUCKET_METRIC_INFECTIOUS_I_DETAIL,
    GEO_BUCKET_METRIC_INFECTIOUS_I_ID,
    GEO_BUCKET_METRIC_PEAK_INFECTIOUS_I_DETAIL,
    GEO_BUCKET_METRIC_PEAK_INFECTIOUS_I_ID,
)
from eosp.services.network import ContactNetwork
from eosp.services.scenarios import ScenarioSpec


PERCENTILES = (2.5, 25.0, 50.0, 75.0, 97.5)


_RISK_METRIC_DETAIL = (
    "Infections among hub contact-cohort agents and in-flight co-location. "
    "Broader community transmission at airports is not modelled in this version."
)


def run_ensemble(
    *,
    scenario: ScenarioSpec,
    inference: InferenceResult,
    network: ContactNetwork,
    seed: SeedState,
    config: EnsembleConfig | None = None,
    posterior_samples: Mapping[str, np.ndarray] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> ForecastResponse:
    config = config or EnsembleConfig()
    started_at = perf_counter()
    scenario_network = scenario.applied_to(network)
    base_params = _summary_to_params(inference)
    scenario_params = scenario.apply_to_parameters(base_params)
    samples = _draw_parameter_samples(
        inference=inference,
        scenario_params=scenario_params,
        n_simulations=config.n_simulations,
        rng_seed=config.rng_seed,
        posterior_samples=posterior_samples,
    )

    trajectories = _run_trajectories(
        network=scenario_network,
        seed=seed,
        samples=samples,
        n_days=config.n_days,
        rng_seed=config.rng_seed,
        parallel=config.parallel,
        max_workers=config.max_workers,
        progress_callback=progress_callback,
        n_simulations=config.n_simulations,
        scenario_name=scenario.name,
    )

    elapsed = round(perf_counter() - started_at, 3)
    points = _aggregate_points(trajectories=trajectories, start_date=config.start_date, n_days=config.n_days)
    geo_forecast = _aggregate_geo_forecast(
        trajectories=trajectories,
        start_date=config.start_date,
        n_days=config.n_days,
    )
    hash_input = f"{config.n_simulations}|{config.rng_seed}|{scenario.name}"
    ensemble_spec_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
    metadata: dict[str, Any] = {
            "model_version": inference.version,
            "n_simulations": config.n_simulations,
            "ensemble_spec_hash": ensemble_spec_hash,
            "risk_metric_detail": _RISK_METRIC_DETAIL,
            "parameter_values": {
                "p_transmit_mean": float(np.mean(samples["p_transmit"])),
                "p_transmit_std": float(np.std(samples["p_transmit"])),
                "contacts_daily_mean": float(np.mean(samples["contacts_daily"])),
                "incubation_mean": float(np.mean(samples["incubation_mean"])),
                "h2h_multiplier_mean": float(np.mean(samples["h2h_multiplier"])),
                "cfr_mean": float(np.mean(samples["cfr"])),
            },
            "execution_time_seconds": elapsed,
            "generated_timestamp": datetime.now(UTC).isoformat(),
            "freshness_status": "current",
            "cached_at": datetime.now(UTC).isoformat(),
            "posterior_version": inference.version,
            "engine": "abm_monte_carlo",
            "n_agents": float(scenario_network.n_agents),
            "scenario_modifications": scenario.network_modifications,
    }
    gw = scenario_network.gateway_weights
    if gw:
        metadata["gateway_weights"] = dict(gw)
        if scenario_network.gateway_pseudocount is not None:
            metadata["gateway_pseudocount"] = float(scenario_network.gateway_pseudocount)
    if scenario_network.itinerary_snapshot_id:
        metadata["flight_snapshot_id"] = scenario_network.itinerary_snapshot_id
    if scenario_network.itinerary_seed_manifest:
        metadata["seed_manifest"] = dict(scenario_network.itinerary_seed_manifest)
    if scenario.parameter_overrides.get("degraded_flight_coverage"):
        metadata["degraded_flight_coverage"] = True
    if seed.seed_scaling is not None:
        metadata["seed_scaling"] = dict(seed.seed_scaling)
    metadata["inference_vs_initial_states_note"] = (
        "Inference drives transmission parameters; initial E/I/R/D counts are case-derived "
        "allocation on agents in the seeded pool (see seed_scaling when totals exceeded the pool)."
    )
    if geo_forecast is not None:
        metadata["geo_forecast"] = geo_forecast
    response = ForecastResponse(
        scenario=scenario.name,
        forecast=points,
        metadata=metadata,
    )
    return response


def _draw_parameter_samples(
    *,
    inference: InferenceResult,
    scenario_params: Mapping[str, float],
    n_simulations: int,
    rng_seed: int,
    posterior_samples: Mapping[str, np.ndarray] | None,
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(rng_seed)
    if posterior_samples is not None:
        return _resample_posterior(posterior_samples, n_simulations, rng, scenario_params)

    samples: dict[str, np.ndarray] = {}
    samples["p_transmit"] = _truncated_normal(
        rng, mean=float(scenario_params.get("p_transmit", inference.parameters["p_transmit"].mean)),
        std=float(inference.parameters["p_transmit"].std), low=0.0, high=1.0, size=n_simulations,
    )
    samples["contacts_daily"] = _truncated_normal(
        rng, mean=float(inference.parameters["contacts_daily"].mean),
        std=float(inference.parameters["contacts_daily"].std), low=0.0, high=20.0, size=n_simulations,
    )
    samples["incubation_mean"] = _truncated_normal(
        rng, mean=float(inference.parameters["incubation"].mean),
        std=float(inference.parameters["incubation"].std), low=1.0, high=30.0, size=n_simulations,
    )
    samples["h2h_multiplier"] = _truncated_normal(
        rng, mean=float(inference.parameters.get("h2h_multiplier", inference.parameters["p_transmit"]).mean if "h2h_multiplier" in inference.parameters else 1.0),
        std=float(inference.parameters["h2h_multiplier"].std) if "h2h_multiplier" in inference.parameters else 0.3,
        low=0.1, high=5.0, size=n_simulations,
    )
    samples["cfr"] = _truncated_normal(
        rng, mean=float(inference.parameters["cfr"].mean) if "cfr" in inference.parameters else 0.40,
        std=float(inference.parameters["cfr"].std) if "cfr" in inference.parameters else 0.05,
        low=0.05, high=0.85, size=n_simulations,
    )
    return samples


_POSTERIOR_KEY_ALIASES = {
    "incubation": "incubation_mean",
}

_REQUIRED_ABM_KEYS = ("p_transmit", "contacts_daily", "incubation_mean", "h2h_multiplier", "cfr")
_DEFAULT_ABM_VALUES = {
    "p_transmit": 0.08,
    "contacts_daily": 2.4,
    "incubation_mean": 8.0,
    "h2h_multiplier": 1.0,
    "cfr": 0.40,
}


def _resample_posterior(
    posterior_samples: Mapping[str, np.ndarray],
    n_simulations: int,
    rng: np.random.Generator,
    scenario_params: Mapping[str, float],
) -> dict[str, np.ndarray]:
    base_length = len(next(iter(posterior_samples.values())))
    indices = rng.integers(0, base_length, size=n_simulations)
    drawn: dict[str, np.ndarray] = {}
    for key, values in posterior_samples.items():
        normalized = _POSTERIOR_KEY_ALIASES.get(key, key)
        drawn[normalized] = np.asarray(values)[indices]
    for key in _REQUIRED_ABM_KEYS:
        if key not in drawn:
            drawn[key] = np.full(n_simulations, _DEFAULT_ABM_VALUES[key], dtype=float)
    if "p_transmit_scale" in scenario_params and scenario_params["p_transmit_scale"] != 1.0:
        drawn["p_transmit"] = drawn["p_transmit"] * float(scenario_params["p_transmit_scale"])
    return drawn


def _truncated_normal(
    rng: np.random.Generator,
    mean: float,
    std: float,
    low: float,
    high: float,
    size: int,
) -> np.ndarray:
    std_clamped = max(float(std), 1e-6)
    raw = rng.normal(loc=float(mean), scale=std_clamped, size=size)
    return np.clip(raw, low, high)


def _run_trajectories(
    *,
    network: ContactNetwork,
    seed: SeedState,
    samples: dict[str, np.ndarray],
    n_days: int,
    rng_seed: int,
    parallel: bool,
    max_workers: int | None,
    progress_callback: Callable[[dict], None] | None = None,
    n_simulations: int = 0,
    scenario_name: str = "",
) -> list[Trajectory]:
    n_simulations = n_simulations or len(samples["p_transmit"])
    BATCH_REPORT = 500
    t_start = perf_counter()

    def run_one(index: int) -> Trajectory:
        sample_rng = np.random.default_rng((rng_seed + index) & 0xFFFFFFFF)
        params = {
            "p_transmit": float(samples["p_transmit"][index]),
            "contacts_daily": float(samples["contacts_daily"][index]),
            "incubation_mean": float(samples["incubation_mean"][index]),
            "h2h_multiplier": float(samples["h2h_multiplier"][index]),
            "cfr": float(samples["cfr"][index]),
        }
        return simulate_trajectory(
            network=network,
            params=params,
            seed=seed,
            n_days=n_days,
            rng=sample_rng,
        )

    if not parallel or n_simulations < 64:
        results: list[Trajectory] = []
        for i in range(n_simulations):
            results.append(run_one(i))
            if progress_callback and (i + 1) % BATCH_REPORT == 0:
                elapsed = perf_counter() - t_start
                progress_callback({
                    "stage": "simulation",
                    "status": "running",
                    "trajectories": i + 1,
                    "total": n_simulations,
                    "scenario": scenario_name,
                    "elapsed_s": round(elapsed, 2),
                    "fan_sample": _sample_fan(results, n_days=n_days, k=10),
                })
        return results

    from concurrent.futures import as_completed

    workers = max_workers or min(8, max(1, (os.cpu_count() or 4)))
    results: list[Trajectory | None] = [None] * n_simulations
    completed_count = 0
    last_reported = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_index = {pool.submit(run_one, i): i for i in range(n_simulations)}
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            results[idx] = future.result()
            completed_count += 1
            if progress_callback and completed_count - last_reported >= BATCH_REPORT:
                last_reported = completed_count
                done_so_far = [r for r in results if r is not None]
                elapsed = perf_counter() - t_start
                progress_callback({
                    "stage": "simulation",
                    "status": "running",
                    "trajectories": completed_count,
                    "total": n_simulations,
                    "scenario": scenario_name,
                    "elapsed_s": round(elapsed, 2),
                    "fan_sample": _sample_fan(done_so_far, n_days=n_days, k=10),
                })
    return cast(list[Trajectory], results)


def _sample_fan(trajectories: list[Trajectory], n_days: int, k: int = 10) -> list[list[float]]:
    """Return k randomly sampled cumulative-case arrays for the fan-chart."""
    if not trajectories:
        return []
    # Fixed seed: keeps the fan-chart sample stable as more trajectories complete
    # during a single run, so the SSE consumer sees a coherent set of curves.
    rng = np.random.default_rng(42)
    chosen = rng.choice(len(trajectories), size=min(k, len(trajectories)), replace=False)
    return [trajectories[i].cumulative_cases[1:].tolist() for i in chosen]


def _aggregate_points(
    *,
    trajectories: Iterable[Trajectory],
    start_date: date,
    n_days: int,
) -> list[ForecastPoint]:
    cumulative_cases = np.stack([trajectory.cumulative_cases for trajectory in trajectories])
    cumulative_deaths = np.stack([trajectory.cumulative_deaths for trajectory in trajectories])
    new_cases = np.stack([trajectory.new_cases_per_day for trajectory in trajectories])

    points: list[ForecastPoint] = []
    for day in range(1, n_days + 1):
        cum_cases_day = cumulative_cases[:, day]
        cum_deaths_day = cumulative_deaths[:, day]
        new_cases_day = new_cases[:, day]
        points.append(
            ForecastPoint(
                day=day,
                date=start_date + timedelta(days=day - 1),
                cases_cumulative=_percentile_block(cum_cases_day, include_50=True),
                cases_new=_percentile_block(new_cases_day, include_50=False),
                deaths_cumulative=_percentile_block(cum_deaths_day, include_50=False),
            )
        )
    return points


def _aggregate_geo_forecast(
    *,
    trajectories: Iterable[Trajectory],
    start_date: date,
    n_days: int,
) -> dict[str, Any] | None:
    traj_list = list(trajectories)
    if not traj_list:
        return None
    first = traj_list[0]
    if not first.geo_bucket_labels or first.bucket_cumulative_infected is None:
        return None
    labels = first.geo_bucket_labels
    try:
        stacked_cum = np.stack([cast(np.ndarray, t.bucket_cumulative_infected) for t in traj_list])
    except ValueError:
        return None
    inf_arrays: list[np.ndarray] = []
    for t in traj_list:
        cum = cast(np.ndarray, t.bucket_cumulative_infected)
        if t.bucket_infectious_I is None:
            inf_arrays.append(np.zeros_like(cum))
        else:
            inf_arrays.append(np.asarray(t.bucket_infectious_I))
    try:
        stacked_inf = np.stack(inf_arrays)
    except ValueError:
        return None
    # (n_sim, n_days+1, n_buckets)
    n_sim = int(stacked_inf.shape[0])
    tot_i_per_day = stacked_inf.sum(axis=2)
    peak_day_per_sim = tot_i_per_day.argmax(axis=1)
    peak_inf_per_sim = stacked_inf[np.arange(n_sim, dtype=np.intp), peak_day_per_sim, :]
    peak_blocks: dict[str, dict[str, float]] = {}
    for bi, label in enumerate(labels):
        peak_blocks[label] = _percentile_block(peak_inf_per_sim[:, bi].astype(float), include_50=True)
    by_day: list[dict[str, Any]] = []
    for day in range(1, n_days + 1):
        buckets: dict[str, Any] = {}
        for bi, label in enumerate(labels):
            day_vals_cum = stacked_cum[:, day, bi].astype(float)
            day_vals_inf = stacked_inf[:, day, bi].astype(float)
            entry: dict[str, Any] = {
                "cumulative_infected": _percentile_block(day_vals_cum, include_50=True),
                "infectious_I": _percentile_block(day_vals_inf, include_50=True),
            }
            if day == n_days:
                entry["peak_infectious_I"] = peak_blocks[label]
            buckets[label] = entry
        by_day.append(
            {
                "day": day,
                "date": (start_date + timedelta(days=day - 1)).isoformat(),
                "buckets": buckets,
            }
        )
    return {
        "bucket_order": list(labels),
        "metrics": [
            {"id": GEO_BUCKET_METRIC_ID, "detail": GEO_BUCKET_METRIC_DETAIL},
            {"id": GEO_BUCKET_METRIC_INFECTIOUS_I_ID, "detail": GEO_BUCKET_METRIC_INFECTIOUS_I_DETAIL},
            {"id": GEO_BUCKET_METRIC_PEAK_INFECTIOUS_I_ID, "detail": GEO_BUCKET_METRIC_PEAK_INFECTIOUS_I_DETAIL},
        ],
        "metric": GEO_BUCKET_METRIC_ID,
        "metric_detail": GEO_BUCKET_METRIC_DETAIL,
        "source": "abm_monte_carlo",
        "distinct_from": (
            "Map heat from /geo/outbreak uses the same bucket medians when a baseline geo_forecast is cached; "
            "other overlays may use different kernels."
        ),
        "by_day": by_day,
    }


def _percentile_block(values: np.ndarray, include_50: bool) -> dict[str, float]:
    p_2_5, p_25, p_50, p_75, p_97_5 = np.percentile(values, PERCENTILES)
    block: dict[str, float] = {
        "median": _round_metric(p_50),
        "ci_95_lower": _round_metric(p_2_5),
        "ci_95_upper": _round_metric(p_97_5),
    }
    if include_50:
        block["ci_50_lower"] = _round_metric(p_25)
        block["ci_50_upper"] = _round_metric(p_75)
    return block


def _round_metric(value: float) -> float:
    return float(round(float(value), 1))


def _summary_to_params(inference: InferenceResult) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, estimate in inference.parameters.items():
        out[name] = float(estimate.mean)
    out.setdefault("p_transmit", out.get("p_transmit", 0.08))
    out.setdefault("contacts_daily", out.get("contacts_daily", 2.4))
    out.setdefault("incubation_mean", float(inference.parameters["incubation"].mean) if "incubation" in inference.parameters else 8.0)
    out.setdefault("h2h_multiplier", out.get("h2h_multiplier", 1.0))
    out.setdefault("cfr", out.get("cfr", 0.40))
    return out


def _cohort_agent_indices(network: ContactNetwork) -> list[int]:
    """Agents eligible for case-anchored seeding.

    Prefer explicit ``cohort_agent`` metadata from the hub world; fall back to
    all non-``ship_member`` agents, then the full roster (legacy spec networks).
    """

    preferred: list[int] = []
    for i, m in enumerate(network.node_metadata):
        if m.get("ship_member"):
            continue
        if m.get("cohort_agent") is False:
            continue
        preferred.append(i)
    if preferred:
        return preferred
    non_ship = [i for i, m in enumerate(network.node_metadata) if not m.get("ship_member")]
    if non_ship:
        return non_ship
    return list(range(network.n_agents))


def seed_state_from_case_records(
    *,
    network: ContactNetwork,
    cases: list[CaseRecord],
    rng_seed: int = 20260507,
) -> SeedState:
    """Allocate initial E/I/R/D from case aggregates on the contact cohort.

    When raw compartment totals exceed the cohort pool, counts are scaled
    proportionally (largest remainder; tie-break order D, I, E, R) and
    ``seed_scaling`` is attached.
    """

    rng = np.random.default_rng(rng_seed)
    cohort_ix = _cohort_agent_indices(network)
    anchor: list[int] = []
    for i in cohort_ix:
        m = network.node_metadata[i]
        hi = str(m.get("home_iata") or "").upper()
        if not hi:
            continue
        for c in cases:
            ca = (c.location_airport_code or "").upper()
            if ca and hi == ca:
                anchor.append(i)
                break
    anch_set = set(anchor)
    rest = [i for i in cohort_ix if i not in anch_set]
    rng.shuffle(rest)
    pool = list(dict.fromkeys(anchor)) + rest
    n_pool = len(pool)

    n_total = int(total_cohort_persons(cases)) if cases else 0
    n_deceased = int(total_death_equivalents(cases)) if cases else 0
    n_observed_alive = max(0, n_total - n_deceased)
    n_recovered = max(0, n_observed_alive - 2)
    n_active = min(2, n_observed_alive)
    hidden_multiplier = 1.5
    e0 = max(4, int(round(n_total * hidden_multiplier))) if cases else max(4, 4)
    i0 = n_active + 1
    r0 = n_recovered
    d0 = n_deceased

    seed_scaling: dict[str, Any] | None = None
    if n_pool <= 0:
        return SeedState(exposed=[], infectious=[], recovered=[], deceased=[], seed_scaling=None)

    s_raw = e0 + i0 + r0 + d0
    if s_raw == 0:
        e, i, r, d = 0, 0, 0, 0
    elif s_raw <= n_pool:
        e, i, r, d = e0, i0, r0, d0
    else:
        scale = n_pool / s_raw
        floats = {"E": e0 * scale, "I": i0 * scale, "R": r0 * scale, "D": d0 * scale}
        floors = {k: int(floats[k]) for k in floats}
        rem = n_pool - sum(floors.values())
        tie_order = ("D", "I", "E", "R")
        ordered = sorted(tie_order, key=lambda k: (-(floats[k] - floors[k]), tie_order.index(k)))
        for j in range(rem):
            floors[ordered[j]] += 1
        e, i, r, d = floors["E"], floors["I"], floors["R"], floors["D"]
        seed_scaling = {
            "method": "proportional_to_pool",
            "n_pool": n_pool,
            "raw": {"E": e0, "I": i0, "R": r0, "D": d0},
            "applied": {"E": e, "I": i, "R": r, "D": d},
        }

    def take(n: int) -> list[int]:
        nn = max(0, min(int(n), len(pool)))
        if nn == 0:
            return []
        chosen = pool[:nn]
        del pool[:nn]
        return chosen

    return SeedState(
        exposed=take(e),
        infectious=take(i),
        recovered=take(r),
        deceased=take(d),
        seed_scaling=seed_scaling,
    )


def seed_state_from_case_counts(
    *,
    network: ContactNetwork,
    n_recent_active: int,
    n_recovered: int,
    n_deceased: int,
    n_recently_exposed: int = 0,
    rng_seed: int = 20260507,
) -> SeedState:
    rng = np.random.default_rng(rng_seed)
    pool = list(_cohort_agent_indices(network))
    rng.shuffle(pool)

    def take(n: int) -> list[int]:
        nn = max(0, min(int(n), len(pool)))
        if nn == 0:
            return []
        chosen = pool[:nn]
        del pool[:nn]
        return chosen

    return SeedState(
        exposed=take(n_recently_exposed),
        infectious=take(n_recent_active),
        recovered=take(n_recovered),
        deceased=take(n_deceased),
    )
