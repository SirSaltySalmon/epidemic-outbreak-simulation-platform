"""EOSP CLI — inference refit only.

Forward simulation uses the hub timeline engine (``eosp.services.hub_timeline``) via
``eosp.services.forecast.build_forecast`` and ``JobManager`` full refresh — not this script.

Use ``--refit`` to run NumPyro NUTS over cases and persist ``InferenceResult``.
To repopulate forecast caches, run a full refresh job or call the forecast API.
"""

from __future__ import annotations

import argparse
import logging
import sys

from eosp.core.compute_config import InferenceConfig
from eosp.core.db import create_session_factory
from eosp.core.repository import SqlRepository
from eosp.services.inference import run_inference
from eosp.services.network import build_default_network


logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EOSP batch worker (inference refit; forward MC via API/job).",
    )
    parser.add_argument(
        "--refit",
        action="store_true",
        help="Required. Run NumPyro NUTS on case timeline and persist posterior summaries.",
    )
    parser.add_argument("--num-warmup", type=int, default=1000)
    parser.add_argument("--num-samples", type=int, default=2000)
    parser.add_argument("--num-chains", type=int, default=4)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not args.refit:
        print(
            "Monte Carlo forecast runs were removed. See docs/ABM_RETIREMENT.md.\n"
            "Run with --refit to execute NumPyro inference only.",
            file=sys.stderr,
        )
        sys.exit(1)

    session_factory = create_session_factory()
    if session_factory is None:
        raise SystemExit("EOSP_DATABASE_URL is not configured. Copy .env.example to .env first.")

    repository = SqlRepository(session_factory=session_factory)
    cases = list(repository.list_cases())
    if not cases:
        raise SystemExit("Cannot refit without at least one case in the database.")

    network = build_default_network(cases=cases)
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
    print(f"Inference stored: {artifacts.result.version}")


if __name__ == "__main__":
    main()
