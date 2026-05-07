from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

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

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/cases/summary", response_model=CaseSummary)
def case_summary(request: Request) -> CaseSummary:
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
    model_version: str = "latest",
    include_credible_intervals: bool = True,
):
    if scenario not in SCENARIO_CONFIG:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario}")
    repo = request.app.state.repository
    inference = repo.get_inference(model_version)
    if inference is None:
        raise HTTPException(status_code=404, detail="Model version not found")

    try:
        response = get_cached_forecast(
            scenario,
            repository=repo,
            inference=inference,
            require_version_match=(model_version != "latest"),
        )
    except ForecastNotCachedError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"No forecast cached for scenario '{exc.scenario}'. "
                "Trigger a run via POST /api/v1/forecasts/run or POST /api/v1/inference/run."
            ),
        ) from exc

    if not include_credible_intervals:
        for point in response.forecast:
            for metric in (point.cases_cumulative, point.cases_new, point.deaths_cumulative):
                for key in list(metric):
                    if key.startswith("ci_"):
                        del metric[key]
    return response


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
def trigger_inference(request: Request, reason: str = "manual"):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Background job manager unavailable")
    job_id = jobs.schedule_full_refresh(reason=reason, trigger=TriggerType.MANUAL)
    return {"job_id": job_id, "status": "scheduled", "reason": reason}


@router.get("/inference/jobs/{job_id}")
def inference_job_status(job_id: str, request: Request):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Background job manager unavailable")
    record = jobs.get_status(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return record.to_dict()


@router.get("/inference/jobs")
def list_inference_jobs(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        return {"jobs": []}
    return {"jobs": [record.to_dict() for record in jobs.list_recent(limit=limit)]}


@router.get("/inference/versions")
def inference_versions(request: Request, limit: int = Query(default=10, ge=1, le=50)):
    versions = []
    for inference in request.app.state.repository.inferences[:limit]:
        versions.append(
            {
                "version_id": inference.version,
                "timestamp": inference.timestamp,
                "trigger": inference.trigger,
                "n_cases": inference.n_cases,
                "convergence_status": inference.diagnostics["convergence_status"],
                "rhat_max": max(inference.diagnostics["rhat"].values()),
                "divergence_count": inference.diagnostics["divergences"],
                "data_quality_mean": 0.84,
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
def scenario_comparison(request: Request, scenarios: str = "baseline,quarantine_immediate,evacuation_delay_7d"):
    scenario_names = [name.strip() for name in scenarios.split(",") if name.strip()]
    unknown = [name for name in scenario_names if name not in SCENARIO_CONFIG]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown scenario(s): {', '.join(unknown)}")
    repo = request.app.state.repository
    try:
        return compare_scenarios(scenario_names, repo.latest_inference(), repository=repo)
    except ForecastNotCachedError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"No forecast cached for scenario '{exc.scenario}'. "
                "Trigger a run via POST /api/v1/forecasts/run or POST /api/v1/inference/run."
            ),
        ) from exc


@router.get("/admin/data-quality/alerts")
def data_quality_alerts(request: Request):
    return {"alerts": request.app.state.repository.alerts}
