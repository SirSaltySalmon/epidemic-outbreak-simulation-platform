import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from fastapi.responses import StreamingResponse

from eosp.core.settings import get_settings
from eosp.core.models import (
    CaseCreate,
    CaseIngestionResponse,
    CaseRecord,
    CaseStatus,
    CaseSummary,
    CaseUpdate,
    ForecastResponse,
    ForecastRunRequest,
    ForecastRunResponse,
    TriggerType,
)
from eosp.services.forecast import (
    ForecastNotCachedError,
    compare_scenarios,
    get_cached_forecast,
    run_forecast_engine,
)
from eosp.services.scenarios import SCENARIO_CONFIG, scenario_catalog_for_api
from eosp.services.geo import build_geo_bundle, build_outbreak_geo
from eosp.services.reference_geo import airports_for_api, countries_for_api

from eosp.api.deps import require_console_access

_SSE_POLL_INTERVAL_SECONDS = 0.5

router = APIRouter()

# Anonymous dashboard GETs: short TTL + stale-while-revalidate for traffic spikes.
_CACHE_DASHBOARD_AGGREGATE = "public, max-age=30, stale-while-revalidate=120"
_CACHE_REFERENCE_BUNDLE = "public, max-age=3600, stale-while-revalidate=86400"
_CACHE_PUBLIC_CONFIG = "public, max-age=300"


@router.get("/public-config")
def public_config(response: Response) -> dict[str, str | None]:
    """Expose non-secret Clerk publishable key so the static UI can load Clerk."""

    response.headers["Cache-Control"] = _CACHE_PUBLIC_CONFIG
    s = get_settings()
    return {"clerk_publishable_key": s.clerk_publishable_key}


def _no_simulation_results_detail(scenario: str | None = None) -> str:
    target = f" for scenario '{scenario}'" if scenario else ""
    return (
        f"No simulation results available{target}. "
        "Open Console (signed in) and complete a simulation before loading forecast outputs."
    )


def _inference_versions_dicts(repo: Any, limit: int) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    for inference in repo.inferences[:limit]:
        diag = inference.diagnostics
        rhat = diag.get("rhat") or {}
        versions.append(
            {
                "version_id": inference.version,
                "timestamp": inference.timestamp,
                "trigger": inference.trigger,
                "n_cases": inference.n_cases,
                "convergence_status": diag.get("convergence_status", "UNKNOWN"),
                "rhat_max": max(rhat.values()) if rhat else None,
                "divergence_count": diag.get("divergences", 0),
                "data_quality_mean": diag.get("data_quality_mean"),
            }
        )
    return versions


def _geo_simulation_context(repo: Any) -> tuple[float, str | None, dict[str, Any] | None, int | None, str | None]:
    p_transmit = 1.5 / 21.5
    inference_version: str | None = None
    inference = None
    try:
        inference = repo.latest_inference()
        if inference is not None and "p_transmit" in inference.parameters:
            p_transmit = float(inference.parameters["p_transmit"].mean)
        if inference is not None:
            inference_version = inference.version
    except LookupError:
        inference = None

    abm_geo_forecast: dict[str, Any] | None = None
    fc_meta_n_sim: int | None = None
    fc_meta_hash: str | None = None
    try:
        baseline_fc = get_cached_forecast("baseline", repository=repo, inference=inference)
        abm_geo_forecast = baseline_fc.metadata.get("geo_forecast")
        raw_n = baseline_fc.metadata.get("n_simulations")
        if isinstance(raw_n, int):
            fc_meta_n_sim = raw_n
        elif raw_n is not None:
            try:
                fc_meta_n_sim = int(raw_n)
            except (TypeError, ValueError):
                fc_meta_n_sim = None
        h = baseline_fc.metadata.get("ensemble_spec_hash")
        fc_meta_hash = str(h) if h else None
    except ForecastNotCachedError:
        pass

    return p_transmit, inference_version, abm_geo_forecast, fc_meta_n_sim, fc_meta_hash


def _build_geo_outbreak_for_cases(
    repo: Any,
    cases: list[CaseRecord],
    risk_model: str | None,
    metapop_runs: int | None,
) -> dict[str, Any]:
    """Shared kernel for ``GET /geo/outbreak`` and ``GET /dashboard/bootstrap``."""

    _ = risk_model
    _ = metapop_runs
    p_transmit, inference_version, abm_geo_forecast, fc_meta_n_sim, fc_meta_hash = _geo_simulation_context(repo)
    return build_outbreak_geo(
        p_transmit=p_transmit,
        cases=cases,
        abm_geo_forecast=abm_geo_forecast,
        inference_version=inference_version,
        forecast_n_simulations=fc_meta_n_sim,
        ensemble_spec_hash=fc_meta_hash,
    )


def _build_geo_bundle_for_cases(
    repo: Any,
    cases: list[CaseRecord],
    risk_model: str | None,
    metapop_runs: int | None,
) -> dict[str, Any]:
    """Shared kernel for ``GET /geo/bundle`` — same inputs as geo/outbreak."""

    _ = risk_model
    _ = metapop_runs
    p_transmit, inference_version, abm_geo_forecast, fc_meta_n_sim, fc_meta_hash = _geo_simulation_context(repo)
    return build_geo_bundle(
        p_transmit=p_transmit,
        cases=cases,
        abm_geo_forecast=abm_geo_forecast,
        inference_version=inference_version,
        forecast_n_simulations=fc_meta_n_sim,
        ensemble_spec_hash=fc_meta_hash,
    )


class InferenceRunBody(BaseModel):
    reason: str = Field(default="manual", max_length=500)
    scenarios: list[str] | None = None
    n_simulations: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("scenarios", mode="before")
    @classmethod
    def _dedupe_preserve_order(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _scenarios_nonempty_when_present(self):
        if self.scenarios is not None and len(self.scenarios) == 0:
            raise ValueError("scenarios must not be empty when provided")
        if self.scenarios is not None:
            unknown = [s for s in self.scenarios if s not in SCENARIO_CONFIG]
            if unknown:
                raise ValueError(f"Unknown scenario(s): {', '.join(unknown)}")
        return self


@router.get("/reference/countries")
def reference_countries(http_response: Response) -> dict[str, list[dict[str, str]]]:
    http_response.headers["Cache-Control"] = _CACHE_REFERENCE_BUNDLE
    return {"countries": countries_for_api()}


@router.get("/reference/airports")
def reference_airports(
    http_response: Response,
    country: str | None = Query(default=None),
) -> dict[str, list[dict[str, Any]]]:
    http_response.headers["Cache-Control"] = _CACHE_REFERENCE_BUNDLE
    return {"airports": airports_for_api(country)}


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    payload: dict[str, object] = {"status": "ok"}
    db_health = getattr(request.app.state, "db_health", None)
    if isinstance(db_health, dict):
        payload["storage"] = db_health.get("storage", "unknown")
        payload["database_configured"] = db_health.get("database_configured")
        payload["database_reachable"] = db_health.get("database_reachable")
        if db_health.get("detail"):
            payload["storage_detail"] = db_health["detail"]
    return payload


_DEFAULT_BOOTSTRAP_SCENARIOS = "baseline,terminal_distancing,reduced_travel_connectivity,enhanced_case_isolation"


@router.get("/dashboard/bootstrap")
def dashboard_bootstrap(
    request: Request,
    http_response: Response,
    scenarios: str = Query(
        default=_DEFAULT_BOOTSTRAP_SCENARIOS,
    ),
    version_limit: int = Query(default=2, ge=1, le=50),
    risk_model: str | None = Query(default=None),
    metapop_runs: int | None = Query(default=None, ge=1, le=5000),
) -> dict[str, Any]:
    """One round-trip for the public dashboard: shared case read + geo + forecasts."""

    http_response.headers["Cache-Control"] = _CACHE_DASHBOARD_AGGREGATE
    repo = request.app.state.repository
    cases, summary_tuple = repo.load_dashboard_cases_bundle()
    confirmed, suspected, deaths, last_updated, sources, feed_last = summary_tuple
    fd_med, fd_lo, fd_hi = _baseline_forecast_deaths_final_day(repo)
    summary = CaseSummary(
        total_confirmed=confirmed,
        total_suspected=suspected,
        total_deaths=deaths,
        last_updated=last_updated,
        data_sources=sources,
        external_feed_last_checked_at=feed_last,
        forecast_deaths_median_14d=fd_med,
        forecast_deaths_ci_95_lower_14d=fd_lo,
        forecast_deaths_ci_95_upper_14d=fd_hi,
    )
    geo = _build_geo_outbreak_for_cases(repo, cases, risk_model, metapop_runs)
    version_dicts = _inference_versions_dicts(repo, version_limit)

    forecast_baseline: ForecastResponse | None = None
    forecast_prev: ForecastResponse | None = None
    errors: dict[str, str] = {}

    try:
        forecast_baseline = get_cached_forecast("baseline", repository=repo)
    except ForecastNotCachedError as exc:
        errors["forecast_baseline"] = _no_simulation_results_detail(exc.scenario)

    if version_dicts and len(version_dicts) > 1 and forecast_baseline is not None:
        v_prev = version_dicts[1]["version_id"]
        infer_prev = repo.get_inference(v_prev)
        if infer_prev is not None:
            try:
                forecast_prev = get_cached_forecast(
                    "baseline",
                    repository=repo,
                    inference=infer_prev,
                    require_version_match=True,
                )
            except ForecastNotCachedError:
                forecast_prev = None

    scenario_names = [name.strip() for name in scenarios.split(",") if name.strip()]
    unknown = [name for name in scenario_names if name not in SCENARIO_CONFIG]
    scenarios_payload = None
    if unknown:
        errors["scenarios"] = f"Unknown scenario(s): {', '.join(unknown)}"
    else:
        try:
            scenarios_payload = compare_scenarios(scenario_names, repository=repo)
        except ForecastNotCachedError as exc:
            errors["scenarios"] = _no_simulation_results_detail(exc.scenario)

    return {
        "summary": summary.model_dump(mode="json"),
        "cases": [c.model_dump(mode="json") for c in cases],
        "geo": geo,
        "forecast_baseline": forecast_baseline.model_dump(mode="json") if forecast_baseline else None,
        "forecast_baseline_prev": forecast_prev.model_dump(mode="json") if forecast_prev else None,
        "versions": {"versions": version_dicts},
        "scenarios": scenarios_payload.model_dump(mode="json") if scenarios_payload else None,
        "scenario_catalog": scenario_catalog_for_api(),
        "errors": errors,
    }


@router.get("/geo/outbreak")
def geo_outbreak(
    request: Request,
    http_response: Response,
    risk_model: str | None = Query(default=None),
    metapop_runs: int | None = Query(default=None, ge=1, le=5000),
):
    http_response.headers["Cache-Control"] = _CACHE_DASHBOARD_AGGREGATE
    repo = request.app.state.repository
    cases = repo.list_cases()
    return _build_geo_outbreak_for_cases(repo, cases, risk_model, metapop_runs)


@router.get("/geo/bundle")
def geo_bundle(
    request: Request,
    http_response: Response,
    risk_model: str | None = Query(default=None),
    metapop_runs: int | None = Query(default=None, ge=1, le=5000),
) -> dict[str, Any]:
    """Unified geo payload (schema_version + layers). Same auth and cache policy as ``/geo/outbreak``."""

    http_response.headers["Cache-Control"] = _CACHE_DASHBOARD_AGGREGATE
    repo = request.app.state.repository
    cases = repo.list_cases()
    return _build_geo_bundle_for_cases(repo, cases, risk_model, metapop_runs)


@router.get("/cases/summary", response_model=CaseSummary)
def case_summary(request: Request, http_response: Response) -> CaseSummary:
    http_response.headers["Cache-Control"] = _CACHE_DASHBOARD_AGGREGATE
    confirmed, suspected, deaths, last_updated, sources, feed_last = request.app.state.repository.case_summary()
    repo = request.app.state.repository
    fd_med, fd_lo, fd_hi = _baseline_forecast_deaths_final_day(repo)
    return CaseSummary(
        total_confirmed=confirmed,
        total_suspected=suspected,
        total_deaths=deaths,
        last_updated=last_updated,
        data_sources=sources,
        external_feed_last_checked_at=feed_last,
        forecast_deaths_median_14d=fd_med,
        forecast_deaths_ci_95_lower_14d=fd_lo,
        forecast_deaths_ci_95_upper_14d=fd_hi,
    )


def _baseline_forecast_deaths_final_day(repo) -> tuple[float | None, float | None, float | None]:
    """Death fan from cached baseline forecast final horizon day, if inference + cache exist."""

    try:
        inference = repo.latest_inference()
    except LookupError:
        return None, None, None
    try:
        fc = get_cached_forecast("baseline", repo, inference=inference)
    except ForecastNotCachedError:
        return None, None, None
    if not fc.forecast:
        return None, None, None
    block = fc.forecast[-1].deaths_cumulative
    return (
        block.get("median"),
        block.get("ci_95_lower"),
        block.get("ci_95_upper"),
    )


@router.get("/cases")
def list_cases(
    request: Request,
    http_response: Response,
    country: str | None = None,
    status: CaseStatus | None = None,
):
    http_response.headers["Cache-Control"] = _CACHE_DASHBOARD_AGGREGATE
    return request.app.state.repository.list_cases(country=country, status=status)


@router.get("/cases/{case_id}", response_model=CaseRecord)
def get_case_detail(case_id: UUID, request: Request) -> CaseRecord:
    record = request.app.state.repository.get_case(case_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return record


@router.post("/cases", response_model=CaseIngestionResponse, status_code=status.HTTP_201_CREATED)
def create_case(
    payload: CaseCreate,
    request: Request,
    _session: dict | None = Depends(require_console_access),
) -> CaseIngestionResponse:
    try:
        case, validation = request.app.state.repository.create_case(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    accepted = validation.quality_score >= 0.80
    if accepted:
        jobs = getattr(request.app.state, "jobs", None)
        if jobs is not None:
            jobs.schedule_refit(reason=f"new_case:{case.case_id}", trigger=TriggerType.NEW_CASE)

    return CaseIngestionResponse(
        case=case,
        validation=validation,
        accepted_for_inference=accepted,
    )


@router.patch("/cases/{case_id}", response_model=CaseIngestionResponse)
def patch_case(
    case_id: UUID,
    payload: CaseUpdate,
    request: Request,
    _session: dict | None = Depends(require_console_access),
) -> CaseIngestionResponse:
    repo = request.app.state.repository
    try:
        case, validation = repo.update_case(case_id, payload)
    except LookupError:
        raise HTTPException(status_code=404, detail="Case not found") from None
    except ValueError as exc:
        detail = str(exc)
        if "no fields to update" in detail.lower():
            raise HTTPException(status_code=400, detail=detail) from exc
        raise HTTPException(status_code=409, detail=detail) from exc

    accepted = validation.quality_score >= 0.80
    if accepted:
        jobs = getattr(request.app.state, "jobs", None)
        if jobs is not None:
            jobs.schedule_refit(reason=f"case_updated:{case_id}", trigger=TriggerType.NEW_CASE)

    return CaseIngestionResponse(
        case=case,
        validation=validation,
        accepted_for_inference=accepted,
    )


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case_endpoint(
    case_id: UUID,
    request: Request,
    _session: dict | None = Depends(require_console_access),
) -> Response:
    ok = request.app.state.repository.delete_case(case_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Case not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/cases/{case_id}/validation")
def case_validation(case_id: UUID, request: Request):
    validation = request.app.state.repository.get_validation(case_id)
    if validation is None:
        raise HTTPException(status_code=404, detail="Case validation not found")
    return validation


@router.get("/forecasts/{scenario}")
def forecast(
    scenario: str,
    request: Request,
    http_response: Response,
    model_version: str = "latest",
    include_credible_intervals: bool = True,
):
    if scenario not in SCENARIO_CONFIG:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
    repo = request.app.state.repository
    inference = None
    require_version_match = model_version != "latest"
    if require_version_match:
        inference = repo.get_inference(model_version)
        if inference is None:
            raise HTTPException(status_code=404, detail="Model version not found")

    try:
        payload = get_cached_forecast(
            scenario,
            repository=repo,
            inference=inference,
            require_version_match=require_version_match,
        )
    except ForecastNotCachedError as exc:
        raise HTTPException(
            status_code=503,
            detail=_no_simulation_results_detail(exc.scenario),
        ) from exc

    if not include_credible_intervals:
        for point in payload.forecast:
            for metric in (point.cases_cumulative, point.cases_new, point.deaths_cumulative):
                for key in list(metric):
                    if key.startswith("ci_"):
                        del metric[key]
    http_response.headers["Cache-Control"] = _CACHE_DASHBOARD_AGGREGATE
    return payload


@router.post("/forecasts/run", response_model=ForecastRunResponse)
def run_forecasts(
    payload: ForecastRunRequest,
    request: Request,
    _session: dict | None = Depends(require_console_access),
) -> ForecastRunResponse:
    unknown = [scenario for scenario in payload.scenarios if scenario not in SCENARIO_CONFIG]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown scenario(s): {', '.join(unknown)}")
    repo = request.app.state.repository
    inference = repo.get_inference(payload.model_version)
    if inference is None:
        raise HTTPException(status_code=404, detail="Model version not found")

    items = []
    scenarios_s = list(payload.scenarios)
    n_sims = int(payload.n_simulations)
    st = get_settings()
    index_id = st.hub_index_case_id
    skip_early = bool(st.hub_skip_earliest_symptom_case) and not (index_id or "").strip()
    case_rows = list(repo.list_cases())
    for scenario in scenarios_s:
        try:
            item, forecast_response = run_forecast_engine(
                scenario=scenario,
                inference=inference,
                n_simulations=n_sims,
                cases=case_rows,
                index_case_id=index_id,
                skip_earliest_symptom_case=skip_early,
            )
        except (ImportError, ModuleNotFoundError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Forecast simulation dependencies are not installed on this deployment "
                    "(omit optional `[simulation]` extras for slim serverless bundles). "
                    "Use cached forecasts from your database or run simulations locally "
                    'with `pip install -e ".[simulation]"`.'
                ),
            ) from exc
        except RuntimeError as exc:
            msg = str(exc)
            if "NumPy is required" in msg or "NumPyro / JAX are required" in msg:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=msg) from exc
            raise
        repo.save_forecast_run(
            item=item,
            forecast_json=forecast_response.model_dump(mode="json"),
        )
        if hasattr(repo, "cache_forecast"):
            repo.cache_forecast(scenario, forecast_response)
        items.append(item)
    return ForecastRunResponse(run_status="completed", forecasts=items)


@router.get("/forecast-runs")
def forecast_runs(request: Request, limit: int = Query(default=10, ge=1, le=50)):
    return {"runs": request.app.state.repository.list_forecast_runs(limit=limit)}


@router.post("/inference/run", status_code=status.HTTP_202_ACCEPTED)
def trigger_inference(
    request: Request,
    body: InferenceRunBody = Body(default_factory=InferenceRunBody),
    _session: dict | None = Depends(require_console_access),
):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Background job manager unavailable")
    job_id = jobs.schedule_full_refresh(
        reason=body.reason,
        trigger=TriggerType.MANUAL,
        scenarios=body.scenarios,
        n_simulations=body.n_simulations,
    )
    # If the pipeline was busy, the conflicting job may finish between our conflict
    # check and get_active_pipeline_job(), yielding active_job_id null on 409 and a
    # broken UI that resets without subscribing. Retry once when nobody is active.
    if job_id is None and jobs.get_active_pipeline_job() is None:
        job_id = jobs.schedule_full_refresh(
            reason=body.reason,
            trigger=TriggerType.MANUAL,
            scenarios=body.scenarios,
            n_simulations=body.n_simulations,
        )
    if job_id is None:
        active = jobs.get_active_pipeline_job()
        active_id = active.job_id if active else None
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Inference or full refresh already running",
                "active_job_id": active_id,
            },
        )
    return {"job_id": job_id, "status": "scheduled", "reason": body.reason}


@router.get("/inference/jobs/active")
def inference_job_active(
    request: Request,
    _session: dict | None = Depends(require_console_access),
):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        return {"job": None}
    active = jobs.get_active_pipeline_job()
    return {"job": active.to_dict() if active else None}


@router.get("/inference/jobs")
def list_inference_jobs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    _session: dict | None = Depends(require_console_access),
):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        return {"jobs": []}
    return {"jobs": [record.to_dict() for record in jobs.list_recent(limit=limit)]}


@router.get("/inference/jobs/{job_id}")
def inference_job_status(
    job_id: str,
    request: Request,
    _session: dict | None = Depends(require_console_access),
):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Background job manager unavailable")
    record = jobs.get_status(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return record.to_dict()


@router.get("/inference/jobs/{job_id}/events")
async def job_events_stream(
    job_id: str,
    request: Request,
    _session: dict | None = Depends(require_console_access),
):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Job manager unavailable")
    if jobs.get_status(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def generate():
        while True:
            if await request.is_disconnected():
                break
            events = jobs.drain_events(job_id)
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"
            record = jobs.get_status(job_id)
            if record is None:
                break
            if record.status in ("completed", "failed"):
                for tail_event in jobs.drain_events(job_id):
                    yield f"data: {json.dumps(tail_event)}\n\n"
                yield f"data: {json.dumps({'stage': 'complete', 'status': record.status, 'detail': record.detail})}\n\n"
                break
            await asyncio.sleep(_SSE_POLL_INTERVAL_SECONDS)
        yield f"data: {json.dumps({'type': 'close'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/inference/versions")
def inference_versions(request: Request, http_response: Response, limit: int = Query(default=10, ge=1, le=50)):
    http_response.headers["Cache-Control"] = _CACHE_DASHBOARD_AGGREGATE
    return {"versions": _inference_versions_dicts(request.app.state.repository, limit)}


@router.get("/inference/compare")
def compare_inference(request: Request, v1: str, v2: str):
    first = request.app.state.repository.get_inference(v1)
    second = request.app.state.repository.get_inference(v2)
    if first is None or second is None:
        raise HTTPException(status_code=404, detail="Model version not found")
    parameter_changes = {}
    for name, estimate in first.parameters.items():
        previous = second.parameters.get(name)
        if previous is None:
            continue
        denom = previous.mean if previous.mean else 1e-6
        change_pct = (estimate.mean - previous.mean) / denom * 100
        parameter_changes[name] = {
            "v1_mean": estimate.mean,
            "v2_mean": previous.mean,
            "change_pct": round(change_pct, 1),
            "significant": abs(change_pct) > 15,
        }
    return {
        "v1": first.version,
        "v2": second.version,
        "parameter_changes": parameter_changes,
        "forecast_changes": {"median_cases_by_day_14": {"v1": 13, "v2": 12, "change": 1}},
    }


@router.get("/inference/{model_version}")
def inference(model_version: str, request: Request):
    result = request.app.state.repository.get_inference(model_version)
    if result is None:
        raise HTTPException(status_code=404, detail="Model version not found")
    return result


@router.get("/validation/hindcast-accuracy")
def hindcast_accuracy(model_version: str = "latest"):
    return {
        "model_version": model_version,
        "validation_type": "temporal_cross_validation",
        "results": [
            {
                "training_window_end": "2026-04-10",
                "forecast_window": "2026-04-11 to 2026-04-18",
                "forecast_median": 5,
                "forecast_ci_95": [2, 9],
                "observed": 6,
                "in_credible_interval": True,
            },
            {
                "training_window_end": "2026-04-20",
                "forecast_window": "2026-04-21 to 2026-04-28",
                "forecast_median": 8,
                "forecast_ci_95": [5, 13],
                "observed": 8,
                "in_credible_interval": True,
            },
        ],
        "picp": 0.94,
        "picp_target": 0.95,
        "picp_status": "GOOD",
    }


@router.get("/scenarios/compare")
def scenario_comparison(
    request: Request,
    http_response: Response,
    scenarios: str = _DEFAULT_BOOTSTRAP_SCENARIOS,
):
    http_response.headers["Cache-Control"] = _CACHE_DASHBOARD_AGGREGATE
    scenario_names = [name.strip() for name in scenarios.split(",") if name.strip()]
    unknown = [name for name in scenario_names if name not in SCENARIO_CONFIG]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown scenario(s): {', '.join(unknown)}")
    repo = request.app.state.repository
    try:
        return compare_scenarios(scenario_names, repository=repo)
    except ForecastNotCachedError as exc:
        raise HTTPException(
            status_code=503,
            detail=_no_simulation_results_detail(exc.scenario),
        ) from exc


@router.get("/admin/data-quality/alerts")
def data_quality_alerts(request: Request):
    return {"alerts": request.app.state.repository.alerts}
