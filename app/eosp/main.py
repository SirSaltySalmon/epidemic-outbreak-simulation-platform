from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from eosp.api.routes import router
from eosp.core.db import create_session_factory
from eosp.core.repository import SqlRepository
from eosp.core.seed_data import repository
from eosp.services.ensemble import EnsembleConfig
from eosp.services.inference import InferenceConfig
from eosp.services.jobs import JobManager
from eosp.services.network import build_default_network
from eosp.services.scenarios import SCENARIO_CONFIG


def _build_repository():
    session_factory = create_session_factory()
    if session_factory is None:
        return repository
    try:
        with session_factory() as session:
            session.execute(text("select 1"))
        return SqlRepository(session_factory=session_factory)
    except Exception:
        return repository


def _ensemble_config() -> EnsembleConfig:
    n_sims = int(os.environ.get("EOSP_ENSEMBLE_SIMULATIONS", "10000"))
    return EnsembleConfig(n_simulations=n_sims, n_days=14, parallel=True)


def _inference_config() -> InferenceConfig:
    return InferenceConfig(
        num_warmup=int(os.environ.get("EOSP_NUTS_WARMUP", "1000")),
        num_samples=int(os.environ.get("EOSP_NUTS_SAMPLES", "2000")),
        num_chains=int(os.environ.get("EOSP_NUTS_CHAINS", "4")),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Construct the JobManager so POST endpoints can dispatch work, but DO NOT
    # schedule any inference, ensemble, or forecast compute at startup. Compute
    # only runs in response to explicit POST /api/v1/inference/run,
    # POST /api/v1/forecasts/run, or a debounced refit when new case data is
    # ingested via POST /api/v1/cases.
    app.state.repository = _build_repository()
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
    try:
        yield
    finally:
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
    app.state.repository = _build_repository()
    app.state.jobs = None
    return app


app = create_app()
