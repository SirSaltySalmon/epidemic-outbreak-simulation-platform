"""Shared runtime wiring for FastAPI lifespan and local tools (no FastAPI import)."""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import text

from eosp.core.compute_config import EnsembleConfig, InferenceConfig
from eosp.core.db import create_session_factory
from eosp.core.repository import SqlRepository, empty_repository
from eosp.services.jobs import JobManager
from eosp.services.scenarios import SCENARIO_CONFIG

logger = logging.getLogger("eosp.bootstrap")


def build_repository_with_status() -> tuple[Any, dict[str, Any]]:
    """Open SqlRepository when Postgres is reachable; else use an empty ephemeral repository."""

    session_factory = create_session_factory()
    if session_factory is None:
        logger.warning(
            "EOSP: EOSP_DATABASE_URL is unset — using an empty in-memory repository; "
            "no cases, inference traces, or forecasts will be available until data is ingested"
        )
        return empty_repository(), {
            "storage": "memory",
            "database_configured": False,
            "database_reachable": None,
            "detail": "EOSP_DATABASE_URL unset",
        }
    try:
        with session_factory() as session:
            session.execute(text("select 1"))
        return SqlRepository(session_factory=session_factory), {
            "storage": "postgres",
            "database_configured": True,
            "database_reachable": True,
            "detail": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "EOSP: database unreachable (%s) — using an empty in-memory repository; "
            "no persisted cases, inference traces, or forecasts are available",
            exc,
        )
        return empty_repository(), {
            "storage": "memory",
            "database_configured": True,
            "database_reachable": False,
            "detail": str(exc),
        }


def ensemble_config_from_env() -> EnsembleConfig:
    n_sims = int(os.environ.get("EOSP_ENSEMBLE_SIMULATIONS", "100"))
    return EnsembleConfig(n_simulations=n_sims, n_days=14, parallel=True)


def inference_config_from_env() -> InferenceConfig:
    # Still parse legacy degree env for compatibility; hub_surrogate_v2 ignores it.
    raw_deg = os.environ.get("EOSP_INFERENCE_MEAN_WEIGHTED_DEGREE", "").strip()
    network_summary = None
    if raw_deg:
        network_summary = {"mean_weighted_degree": float(raw_deg)}
    return InferenceConfig(
        num_warmup=int(os.environ.get("EOSP_NUTS_WARMUP", "1000")),
        num_samples=int(os.environ.get("EOSP_NUTS_SAMPLES", "2000")),
        num_chains=int(os.environ.get("EOSP_NUTS_CHAINS", "4")),
        network_summary=network_summary,
    )


def create_default_job_manager(repository: Any) -> JobManager:
    return JobManager(
        repository=repository,
        scenarios=SCENARIO_CONFIG,
        max_workers=2,
        debounce_seconds=30.0,
        ensemble_config=ensemble_config_from_env(),
        inference_config=inference_config_from_env(),
    )
