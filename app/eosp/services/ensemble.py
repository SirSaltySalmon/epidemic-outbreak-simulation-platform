"""Monte Carlo ensemble orchestrator (FR-3.4).

Samples ``n_simulations`` parameter draws from the inferred posterior
(Normal/Beta approximation when only summary stats are available, or the raw
posterior samples when they were persisted), runs the vectorized SEIR+D ABM,
and aggregates daily percentiles into the existing ``ForecastResponse``
schema. Parallelization uses a ``ThreadPoolExecutor`` because the per-step
matrix-vector products release the GIL.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Iterable, Mapping

import numpy as np

from eosp.core.models import ForecastPoint, ForecastResponse, InferenceResult
from eosp.services.abm import SeedState, Trajectory, simulate_trajectory
from eosp.services.network import ContactNetwork
from eosp.services.scenarios import ScenarioSpec


PERCENTILES = (2.5, 25.0, 50.0, 75.0, 97.5)


@dataclass
class EnsembleConfig:
    n_simulations: int = 10000
    n_days: int = 14
    start_date: date = date(2026, 5, 7)
    rng_seed: int = 20260507
    parallel: bool = True
    max_workers: int | None = None


def run_ensemble(
    *,
    scenario: ScenarioSpec,
    inference: InferenceResult,
    network: ContactNetwork,
    seed: SeedState,
    config: EnsembleConfig | None = None,
    posterior_samples: Mapping[str, np.ndarray] | None = None,
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
    )

    elapsed = round(perf_counter() - started_at, 3)
    points = _aggregate_points(trajectories=trajectories, start_date=config.start_date, n_days=config.n_days)
    response = ForecastResponse(
        scenario=scenario.name,
        forecast=points,
        metadata={
            "model_version": inference.version,
            "n_simulations": config.n_simulations,
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
        },
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
) -> list[Trajectory]:
    n_simulations = len(samples["p_transmit"])

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
        return [run_one(index) for index in range(n_simulations)]

    workers = max_workers or min(8, max(1, (os.cpu_count() or 4)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_one, range(n_simulations)))
    return results


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
    ship_indices = network.ship_node_indices() or list(range(network.n_agents))
    pool = list(ship_indices)
    rng.shuffle(pool)

    def take(n: int) -> list[int]:
        n = max(0, min(n, len(pool)))
        if n == 0:
            return []
        chosen = pool[:n]
        del pool[:n]
        return chosen

    return SeedState(
        exposed=take(n_recently_exposed),
        infectious=take(n_recent_active),
        recovered=take(n_recovered),
        deceased=take(n_deceased),
    )
