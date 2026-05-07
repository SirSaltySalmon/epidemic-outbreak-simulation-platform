import os

os.environ.setdefault("EOSP_DISABLE_STARTUP_REFIT", "1")

from fastapi.testclient import TestClient
import pytest

from eosp.main import app
from eosp.scripts.seed_dev_data import main as seed_dev_data
from eosp.services.forecast import _FORECAST_CACHE, prewarm_forecast_cache


client = TestClient(app)


def _ensure_forecast_cache_populated() -> None:
    repo = getattr(app.state, "repository", None)
    if repo is None:
        return
    try:
        inference = repo.latest_inference()
    except Exception:
        return
    try:
        prewarm_forecast_cache(repo, inference)
    except Exception:
        pass


def _clear_forecast_cache_everywhere() -> None:
    """Clear both the in-memory dict cache and any DB-backed forecast cache."""

    _FORECAST_CACHE.clear()
    repo = getattr(app.state, "repository", None)
    if repo is None:
        return
    if hasattr(repo, "forecast_cache"):
        repo.forecast_cache.clear()
    session_factory = getattr(repo, "session_factory", None)
    if session_factory is not None:
        try:
            from eosp.core.tables import ForecastResultRow

            with session_factory() as session:
                session.query(ForecastResultRow).delete()
                session.commit()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def reset_dev_database_if_available():
    _FORECAST_CACHE.clear()
    if hasattr(app.state, "repository") and hasattr(app.state.repository, "forecast_cache"):
        app.state.repository.forecast_cache.clear()
    try:
        seed_dev_data()
    except Exception:
        pass
    _ensure_forecast_cache_populated()
    yield
    _FORECAST_CACHE.clear()
    if hasattr(app.state, "repository") and hasattr(app.state.repository, "forecast_cache"):
        app.state.repository.forecast_cache.clear()
    try:
        seed_dev_data()
    except Exception:
        pass
    _ensure_forecast_cache_populated()


def test_health_check():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_case_summary_matches_seed_outbreak():
    response = client.get("/api/v1/cases/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_confirmed"] == 3
    assert payload["total_suspected"] == 5
    assert payload["total_deaths"] == 3
    assert payload["data_sources"]["WHO_DON"] == 4


def test_forecast_includes_credible_intervals():
    response = client.get("/api/v1/forecasts/baseline")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario"] == "baseline"
    assert len(payload["forecast"]) == 14
    final_cases = payload["forecast"][-1]["cases_cumulative"]
    assert final_cases["ci_95_lower"] <= final_cases["median"] <= final_cases["ci_95_upper"]
    assert 3 <= final_cases["median"] <= 60
    assert payload["metadata"]["freshness_status"] in {"current", "provisional"}
    assert payload["metadata"]["posterior_version"] == payload["metadata"]["model_version"]


def test_forecast_metadata_carries_inferred_parameters():
    response = client.get("/api/v1/forecasts/baseline")
    payload = response.json()
    parameter_values = payload["metadata"]["parameter_values"]
    assert parameter_values["p_transmit_mean"] > 0
    assert parameter_values["contacts_daily_mean"] > 0
    assert parameter_values["incubation_mean"] > 0


def test_scenario_comparison_reports_baseline_delta():
    response = client.get("/api/v1/scenarios/compare?scenarios=baseline,quarantine_immediate")
    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    assert scenarios[0]["vs_baseline"] is None
    assert scenarios[1]["vs_baseline"]["case_change_pct"] <= 0


def test_validation_endpoint_for_case():
    cases = client.get("/api/v1/cases").json()
    response = client.get(f"/api/v1/cases/{cases[0]['case_id']}/validation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["quality_score"] >= 0.65
    assert "temporal_consistency" in payload["checks_passed"]


def test_create_case_persists_and_returns_validation():
    response = client.post(
        "/api/v1/cases",
        json={
            "patient_identifier": "anon_test_ingest",
            "symptom_onset_date": "2026-05-01",
            "hospitalization_date": "2026-05-03",
            "death_date": None,
            "location_country": "ES",
            "location_airport_code": "TFN",
            "confirmed_or_suspected": "suspected",
            "lab_test_result": "not_tested",
            "contacts": [{"id": "anon_p0008", "type": "social"}],
            "data_source": "Manual_Form",
            "updated_by": "pytest",
            "updated_reason": "New_case",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["case"]["patient_identifier"] == "anon_test_ingest"
    assert payload["validation"]["quality_score"] >= 0.65
    assert "accepted_for_inference" in payload

    case_id = payload["case"]["case_id"]
    validation_response = client.get(f"/api/v1/cases/{case_id}/validation")
    assert validation_response.status_code == 200


def test_run_forecast_engine_persists_results():
    response = client.post(
        "/api/v1/forecasts/run",
        json={
            "scenarios": ["baseline", "quarantine_immediate"],
            "model_version": "latest",
            "n_simulations": 250,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_status"] == "completed"
    assert len(payload["forecasts"]) == 2
    assert payload["forecasts"][0]["cases_day_14"]["median"] >= 0

    runs_response = client.get("/api/v1/forecast-runs?limit=5")
    assert runs_response.status_code == 200
    runs = runs_response.json()["runs"]
    assert len(runs) >= 2


def test_inference_jobs_endpoint_handles_missing_manager():
    response = client.get("/api/v1/inference/jobs")
    assert response.status_code == 200
    assert "jobs" in response.json()


def test_forecast_returns_503_when_cache_empty():
    _clear_forecast_cache_everywhere()
    response = client.get("/api/v1/forecasts/baseline")
    assert response.status_code == 503
    assert "POST" in response.json()["detail"]


def test_scenario_compare_returns_503_when_cache_empty():
    _clear_forecast_cache_everywhere()
    response = client.get("/api/v1/scenarios/compare?scenarios=baseline,quarantine_immediate")
    assert response.status_code == 503
