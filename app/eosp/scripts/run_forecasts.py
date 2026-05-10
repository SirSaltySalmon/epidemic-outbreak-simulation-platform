"""Command line driver for the hybrid ABM + Bayesian forecaster.

Two modes:
- Default: run the Monte Carlo ensemble for a list of scenarios against the
  latest persisted posterior.
- ``--refit``: first run NumPyro NUTS over the validated case timeline to
  produce a fresh ``InferenceResult``, then run the ensembles.

Both modes persist the resulting forecasts to ``forecast_results`` and the
in-memory cache so subsequent GET requests serve them instantly.
"""

from __future__ import annotations

import argparse
import logging

from eosp.core.db import create_session_factory
from eosp.core.repository import SqlRepository
from eosp.services.forecast import FULL_SIMULATIONS, run_forecast_engine
from eosp.services.inference import InferenceConfig, run_inference
from eosp.services.network import build_default_network
from eosp.services.scenarios import SCENARIO_CONFIG


logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EOSP forecast scenarios end-to-end.")
    parser.add_argument(
        "--scenarios",
        default="baseline,terminal_distancing,reduced_travel_connectivity,enhanced_case_isolation",
        help="Comma-separated scenario names.",
    )
    parser.add_argument("--model-version", default="latest")
    parser.add_argument("--n-simulations", type=int, default=FULL_SIMULATIONS)
    parser.add_argument("--refit", action="store_true", help="Run NumPyro NUTS before the ensembles.")
    parser.add_argument("--num-warmup", type=int, default=1000)
    parser.add_argument("--num-samples", type=int, default=2000)
    parser.add_argument("--num-chains", type=int, default=4)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    session_factory = create_session_factory()
    if session_factory is None:
        raise SystemExit("EOSP_DATABASE_URL is not configured. Copy .env.example to .env first.")

    repository = SqlRepository(session_factory=session_factory)

    if args.refit:
        cases = list(repository.list_cases())
        if not cases:
            raise SystemExit("Cannot refit without at least one case in the database.")
        network = build_default_network()
        artifacts = run_inference(
            cases=cases,
            network=network,
            config=InferenceConfig(
                num_warmup=args.num_warmup,
                num_samples=args.num_samples,
                num_chains=args.num_chains,
            ),
        )
        repository.update_inference(artifacts.result)
        logger.info(
            "Refit complete: %s convergence=%s rhat_max=%.4f divergences=%d",
            artifacts.result.version,
            artifacts.result.diagnostics["convergence_status"],
            max(artifacts.result.diagnostics["rhat"].values()),
            artifacts.result.diagnostics["divergences"],
        )
        inference = artifacts.result
    else:
        inference = repository.get_inference(args.model_version)
        if inference is None:
            raise SystemExit(f"Model version not found: {args.model_version}")

    scenarios = [scenario.strip() for scenario in args.scenarios.split(",") if scenario.strip()]
    unknown = [scenario for scenario in scenarios if scenario not in SCENARIO_CONFIG]
    if unknown:
        raise SystemExit(f"Unknown scenario(s): {', '.join(unknown)}")

    for scenario in scenarios:
        item, forecast = run_forecast_engine(scenario, inference, n_simulations=args.n_simulations)
        repository.save_forecast_run(item, forecast.model_dump(mode="json"))
        repository.cache_forecast(scenario, forecast)
        median = item.cases_day_14["median"]
        ci_lower = item.cases_day_14["ci_95_lower"]
        ci_upper = item.cases_day_14["ci_95_upper"]
        print(
            f"{scenario}: day-14 cases median={median} "
            f"ci95=[{ci_lower}, {ci_upper}] forecast_id={item.forecast_id}"
        )


if __name__ == "__main__":
    main()
