"""Monte Carlo ensemble → :class:`~eosp.core.models.ForecastResponse`."""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from uuid import uuid4

import numpy as np

from eosp.core.models import (
    CaseRecord,
    ForecastPoint,
    ForecastResponse,
    ForecastRunItem,
    InferenceResult,
)
from dataclasses import replace
from eosp.services.hub_timeline.config import HubTimelineSimulatorConfig, default_hub_timeline_config
from eosp.services.hub_timeline.geo_output import build_geo_forecast_dict
from eosp.services.hub_timeline.kernel import HubTrajectorySnapshot, WorldGraphBundle, simulate_trajectory
from eosp.services.hub_timeline.replay import build_replay_geo
from eosp.services.hub_timeline.routes_graph import build_world_graph
from eosp.services.hub_timeline.timeline import compute_simulation_calendar, eligible_hub_cases
from eosp.services.scenarios import ScenarioSpec, get_scenario


def _world_paths() -> tuple[Path, Path]:
    from eosp.services.world_builder import _default_airports_path, _default_routes_path

    return _default_routes_path(), _default_airports_path()


def _scenario_config(
    spec: ScenarioSpec, base: HubTimelineSimulatorConfig, inference: InferenceResult
) -> HubTimelineSimulatorConfig:
    cfg = base.merged_with_scenario_hub_timeline(spec.hub_timeline)
    infer_cd = float(
        inference.parameters["contacts_daily"].mean
        if "contacts_daily" in inference.parameters
        else base.baseline_contacts_daily
    )
    base_floats = {k: float(v.mean) for k, v in inference.parameters.items()}
    base_floats.setdefault("contacts_daily", infer_cd)
    base_floats.setdefault("cfr", float(cfg.default_cfr))
    base_floats.setdefault("p_transmit", 0.08)
    adj = spec.apply_to_parameters(base_floats)
    scenario_cd = float(adj.get("contacts_daily", infer_cd))
    scale = scenario_cd / max(cfg.baseline_contacts_daily, 1e-9)
    cfg = replace(
        cfg,
        mu_travel=cfg.mu_travel * scale,
        mu_stay=cfg.mu_stay * scale,
        mu_onset_burst=cfg.mu_onset_burst * scale,
    )
    mw = spec.network_modifications.get("modify_weights") or {}
    itin = mw.get("itinerary_patch")
    if itin is not None:
        cfg = replace(cfg, alpha_load=cfg.alpha_load * float(itin))
    return cfg


def _inference_param_draws(
    inference: InferenceResult, spec: ScenarioSpec, cfg: HubTimelineSimulatorConfig
) -> dict[str, tuple[float, float]]:
    def pe(name: str, default_m: float, default_s: float) -> tuple[float, float]:
        v = inference.parameters.get(name)
        if v is None:
            return (default_m, default_s)
        return (float(v.mean), float(max(v.std, 1e-9)))

    base_map: dict[str, float] = {}
    for k, v in inference.parameters.items():
        base_map[k] = float(v.mean)
    if "cfr" not in base_map:
        base_map["cfr"] = float(cfg.default_cfr)
    if "contacts_daily" not in base_map:
        base_map["contacts_daily"] = float(cfg.baseline_contacts_daily)

    adj = spec.apply_to_parameters(base_map)
    p_mean = float(adj.get("p_transmit", base_map.get("p_transmit", 0.08)))
    p_std = float(inference.parameters["p_transmit"].std) if "p_transmit" in inference.parameters else 0.02

    cfr_mean = float(adj.get("cfr", base_map["cfr"]))
    cfr_std = float(inference.parameters["cfr"].std) if "cfr" in inference.parameters else 0.01

    incub_m, incub_s = pe("incubation", 8.0, 2.0)
    h2h_m, h2h_s = pe("h2h_multiplier", 1.0, 0.3)

    return {
        "p_transmit": (p_mean, max(p_std, 1e-9)),
        "h2h_multiplier": (h2h_m, h2h_s),
        "cfr": (cfr_mean, max(cfr_std, 1e-9)),
        "incubation": (incub_m, incub_s),
    }


def run_hub_timeline_forecast(
    *,
    scenario_name: str,
    cases: list[CaseRecord],
    inference: InferenceResult,
    n_simulations: int,
    rng_seed: int,
    index_case_id: str | None = None,
    max_workers: int | None = None,
) -> ForecastResponse:
    routes_path, airports_path = _world_paths()
    allowed, outbound, iata_to_country, dest_weights = build_world_graph(routes_path, airports_path)
    world = WorldGraphBundle(
        allowed=allowed,
        outbound=outbound,
        dest_weights=dest_weights,
        iata_to_country=iata_to_country,
    )

    spec = get_scenario(scenario_name)
    base_cfg = default_hub_timeline_config()
    cfg = _scenario_config(spec, base_cfg, inference)
    params = _inference_param_draws(inference, spec, cfg)

    eligible = eligible_hub_cases(list(cases), allowed_iatas=allowed, index_case_id=index_case_id)
    if not eligible:
        raise ValueError("No eligible hub cases after IATA filter — cannot run hub timeline forecast")

    anchor, sim_days = compute_simulation_calendar(eligible, horizon_days=cfg.horizon_days)

    workers = min(max_workers or (os.cpu_count() or 4), 16)

    def one(seed: int) -> HubTrajectorySnapshot:
        rng = np.random.default_rng(int(seed))
        return simulate_trajectory(
            eligible,
            inference_params=params,
            world=world,
            config=cfg,
            rng=rng,
            sim_days=sim_days,
        )

    seeds = [rng_seed + int(i) * 100_003 for i in range(n_simulations)]
    trajs: list[HubTrajectorySnapshot] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(one, s): s for s in seeds}
        for fut in as_completed(futs):
            trajs.append(fut.result())

    n_days = len(sim_days)
    stack_cases = np.stack([t.global_cases for t in trajs], axis=0)
    stack_deaths = np.stack([t.global_deaths for t in trajs], axis=0)

    def pct_block(arr: np.ndarray) -> dict[str, float]:
        s = np.asarray(arr, dtype=float)
        return {
            "median": float(np.median(s)),
            "p2_5": float(np.percentile(s, 2.5)),
            "p25": float(np.percentile(s, 25.0)),
            "p75": float(np.percentile(s, 75.0)),
            "p97_5": float(np.percentile(s, 97.5)),
            "ci_95_lower": float(np.percentile(s, 2.5)),
            "ci_95_upper": float(np.percentile(s, 97.5)),
        }

    forecast_points: list[ForecastPoint] = []
    prev_cases = np.zeros(stack_cases.shape[0])
    for i in range(n_days):
        day_cases = stack_cases[:, i]
        day_deaths = stack_deaths[:, i]
        new_c = np.maximum(day_cases - prev_cases, 0.0)
        prev_cases = day_cases
        forecast_points.append(
            ForecastPoint(
                day=i + 1,
                date=sim_days[i],
                cases_cumulative=pct_block(day_cases),
                cases_new=pct_block(new_c),
                deaths_cumulative=pct_block(day_deaths),
            )
        )

    geo = build_geo_forecast_dict(trajs, iata_to_country=iata_to_country)
    replay = build_replay_geo(trajs, anchor_date_str=anchor.isoformat())

    hkey = hashlib.sha256(
        f"{scenario_name}|{n_simulations}|{rng_seed}|{inference.version}|{cfg.horizon_days}".encode()
    ).hexdigest()[:16]

    meta = {
        "engine": "hub_timeline_monte_carlo",
        "ensemble_spec_hash": hkey,
        "n_simulations": n_simulations,
        "inference_version": inference.version,
        "rng_seed": rng_seed,
        "geo_forecast": geo,
        "replay_geo": replay,
        "scenario_hub_timeline": {
            "mu_travel": cfg.mu_travel,
            "mu_stay": cfg.mu_stay,
            "mu_onset_burst": cfg.mu_onset_burst,
            "r_nb": cfg.r_nb,
            "alpha_load": cfg.alpha_load,
            "p_stay_when_at_home": cfg.p_stay_when_at_home,
            "p_return_when_away": cfg.p_return_when_away,
            "horizon_days": cfg.horizon_days,
        },
        "anchor_date": anchor.isoformat(),
    }

    return ForecastResponse(scenario=scenario_name, forecast=forecast_points, metadata=meta)


def run_forecast_simulation(
    *,
    scenario: str,
    inference: InferenceResult,
    cases: list[CaseRecord],
    n_simulations: int,
    rng_seed: int | None = None,
    index_case_id: str | None = None,
) -> tuple[ForecastRunItem, ForecastResponse]:
    from eosp.core.compute_config import EnsembleConfig

    ec = EnsembleConfig()
    seed = int(rng_seed if rng_seed is not None else ec.rng_seed)
    t0 = time.perf_counter()
    forecast = run_hub_timeline_forecast(
        scenario_name=scenario,
        cases=cases,
        inference=inference,
        n_simulations=n_simulations,
        rng_seed=seed,
        index_case_id=index_case_id,
    )
    elapsed = time.perf_counter() - t0
    final = forecast.forecast[-1]
    item = ForecastRunItem(
        forecast_id=uuid4(),
        scenario=scenario,
        model_version=inference.version,
        n_simulations=n_simulations,
        execution_time_seconds=float(elapsed),
        cases_day_14={k: float(v) for k, v in final.cases_cumulative.items()},
        deaths_day_14={k: float(v) for k, v in final.deaths_cumulative.items()},
    )
    return item, forecast
