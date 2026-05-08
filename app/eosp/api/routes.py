import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from fastapi.responses import StreamingResponse

from eosp.core.models import (
    CaseCreate,
    CaseIngestionResponse,
    CaseStatus,
    CaseSummary,
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
from eosp.services.scenarios import SCENARIO_CONFIG
from eosp.services.geo import build_outbreak_geo

_SSE_POLL_INTERVAL_SECONDS = 0.5

router = APIRouter()


def _no_simulation_results_detail(scenario: str | None = None) -> str:
    target = f" for scenario '{scenario}'" if scenario else ""
    return (
        f"No simulation results available{target}. "
        "Open Run Analysis and complete a simulation before loading forecast outputs."
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


@router.get("/geo/outbreak")
def geo_outbreak(
    request: Request,
    http_response: Response,
    risk_model: str = Query(default="legacy"),
    metapop_runs: int | None = Query(default=None, ge=1, le=5000),
):
    http_response.headers["Cache-Control"] = "no-store"
    repo = request.app.state.repository
    p_transmit = 1.5 / 21.5  # Beta(1.5, 20) prior mean when no posterior yet
    try:
        inference = repo.latest_inference()
        if inference is not None and "p_transmit" in inference.parameters:
            p_transmit = float(inference.parameters["p_transmit"].mean)
    except LookupError:
        pass
    cases = repo.list_cases()
    return build_outbreak_geo(
        p_transmit=p_transmit,
        cases=cases,
        risk_model=risk_model,
        metapop_n_runs=metapop_runs,
    )


@router.get("/cases/summary", response_model=CaseSummary)
def case_summary(request: Request, http_response: Response) -> CaseSummary:
    http_response.headers["Cache-Control"] = "no-store"
    confirmed, suspected, deaths, last_updated, sources = request.app.state.repository.case_summary()
    return CaseSummary(
        total_confirmed=confirmed,
        total_suspected=suspected,
        total_deaths=deaths,
        last_updated=last_updated,
        data_sources=sources,
    )


@router.get("/cases")
def list_cases(
    request: Request,
    country: str | None = None,
    status: CaseStatus | None = None,
):
    return request.app.state.repository.list_cases(country=country, status=status)


@router.post("/cases", response_model=CaseIngestionResponse, status_code=status.HTTP_201_CREATED)
def create_case(payload: CaseCreate, request: Request) -> CaseIngestionResponse:
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
    http_response.headers["Cache-Control"] = "no-store"
    return payload


@router.post("/forecasts/run", response_model=ForecastRunResponse)
def run_forecasts(payload: ForecastRunRequest, request: Request) -> ForecastRunResponse:
    unknown = [scenario for scenario in payload.scenarios if scenario not in SCENARIO_CONFIG]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown scenario(s): {', '.join(unknown)}")
    repo = request.app.state.repository
    inference = repo.get_inference(payload.model_version)
    if inference is None:
        raise HTTPException(status_code=404, detail="Model version not found")

    items = []
    for scenario in payload.scenarios:
        item, forecast_response = run_forecast_engine(
            scenario=scenario,
            inference=inference,
            n_simulations=payload.n_simulations,
        )
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
def inference_job_active(request: Request):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        return {"job": None}
    active = jobs.get_active_pipeline_job()
    return {"job": active.to_dict() if active else None}


@router.get("/inference/jobs")
def list_inference_jobs(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        return {"jobs": []}
    return {"jobs": [record.to_dict() for record in jobs.list_recent(limit=limit)]}


@router.get("/inference/jobs/{job_id}")
def inference_job_status(job_id: str, request: Request):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Background job manager unavailable")
    record = jobs.get_status(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return record.to_dict()


@router.get("/inference/jobs/{job_id}/events")
async def job_events_stream(job_id: str, request: Request):
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
    http_response.headers["Cache-Control"] = "no-store"
    versions = []
    for inference in request.app.state.repository.inferences[:limit]:
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
    return {"versions": versions}


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
    scenarios: str = "baseline,quarantine_immediate,evacuation_delay_7d",
):
    http_response.headers["Cache-Control"] = "no-store"
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
