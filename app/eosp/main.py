from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from eosp.api.routes import router
from eosp.core.db import create_session_factory
from eosp.core.models import TriggerType
from eosp.core.repository import SqlRepository, empty_repository
from eosp.core.settings import get_settings
from eosp.services.ensemble import EnsembleConfig
from eosp.services.inference import InferenceConfig
from eosp.services.jobs import JobManager
from eosp.services.network import build_default_network
from eosp.services.scenarios import SCENARIO_CONFIG
from eosp.services.who_don_hub_cycle import run_who_don_hub_ingest_cycle

logger = logging.getLogger("eosp.main")


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


def _ensemble_config() -> EnsembleConfig:
    n_sims = int(os.environ.get("EOSP_ENSEMBLE_SIMULATIONS", "10000"))
    return EnsembleConfig(n_simulations=n_sims, n_days=14, parallel=True)


def _inference_config() -> InferenceConfig:
    return InferenceConfig(
        num_warmup=int(os.environ.get("EOSP_NUTS_WARMUP", "1000")),
        num_samples=int(os.environ.get("EOSP_NUTS_SAMPLES", "2000")),
        num_chains=int(os.environ.get("EOSP_NUTS_CHAINS", "4")),
    )


async def _who_don_polling_loop(app: FastAPI) -> None:
    """Deprecated default: automated WHO DON ingest. Prefer manual case entry.

    Runs only when ``EOSP_WHO_DON_POLL_ENABLED`` is true (off by default).
    """
    settings = get_settings()
    if not settings.who_don_poll_enabled:
        return
    interval = max(60.0, float(settings.who_don_poll_interval_seconds))
    await asyncio.sleep(30.0)
    while True:
        jobs = getattr(app.state, "jobs", None)
        if jobs is None:
            await asyncio.sleep(interval)
            continue
        try:
            data_changed = await asyncio.to_thread(
                run_who_don_hub_ingest_cycle,
                app.state.repository,
                hub_url=settings.who_don_url,
                base_feed_key=settings.who_don_feed_key,
                item_policy=settings.who_don_item_policy,
            )
            if data_changed:
                job_id = jobs.schedule_full_refresh(
                    reason="who_don_page_updated",
                    trigger=TriggerType.EXTERNAL_FEED,
                )
                if job_id:
                    logger.info("WHO DON data updated; scheduled full refresh %s", job_id)
                else:
                    logger.info("WHO DON data updated; full refresh not scheduled (pipeline busy)")
        except Exception:
            logger.exception("WHO DON polling failed; will retry after interval")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Construct the JobManager so POST endpoints can dispatch work, but DO NOT
    # schedule any inference, ensemble, or forecast compute at startup. Compute
    # only runs in response to explicit POST /api/v1/inference/run,
    # POST /api/v1/forecasts/run, or a debounced refit when new case data is
    # ingested via POST /api/v1/cases.
    repo, db_health = build_repository_with_status()
    app.state.repository = repo
    app.state.db_health = db_health
    app.state.network = build_default_network()
    job_manager = JobManager(
        repository=app.state.repository,
        network=app.state.network,
        scenarios=SCENARIO_CONFIG,
        max_workers=2,
        debounce_seconds=30.0,
        ensemble_config=_ensemble_config(),
        inference_config=_inference_config(),
    )
    app.state.jobs = job_manager
    poll_task: asyncio.Task[None] | None = None
    if get_settings().who_don_poll_enabled:
        poll_task = asyncio.create_task(_who_don_polling_loop(app))
    try:
        yield
    finally:
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
        job_manager.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Epidemic Outbreak Simulation Platform",
        version="0.2.0",
        description=(
            "Hybrid ABM + Bayesian inference forecaster for the May 2026 Andes hantavirus outbreak. "
            "NumPyro NUTS posteriors feed a vectorized SEIR+D Monte Carlo ensemble over a NetworkX "
            "contact graph; results are cached and refreshed by background jobs."
        ),
        lifespan=lifespan,
    )
    app.include_router(router, prefix="/api/v1")
    app.mount("/", StaticFiles(directory="app/eosp/static", html=True), name="static")
    # Fallback repository so the app is usable even without the lifespan context
    # active (e.g. ``TestClient(app)`` without ``with`` block). The lifespan
    # handler will replace it with the live repository + JobManager bindings.
    repo, db_health = build_repository_with_status()
    app.state.repository = repo
    app.state.db_health = db_health
    app.state.jobs = None
    return app


app = create_app()
