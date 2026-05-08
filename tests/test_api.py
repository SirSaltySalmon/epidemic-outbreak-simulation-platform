from pathlib import Path
from shutil import copyfile

from fastapi.testclient import TestClient
import pytest

from eosp.core.repository import InMemoryRepository
from eosp.core.seed_data import ALERTS, CASES, INFERENCES, VALIDATIONS
from eosp.core.settings import get_settings
from eosp.main import app
from eosp.services.forecast import _FORECAST_CACHE


client = TestClient(app)


@pytest.fixture
def enable_metapop(monkeypatch):
    monkeypatch.setenv("EOSP_METAPOP_ENABLED", "true")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("EOSP_METAPOP_ENABLED", raising=False)
    get_settings.cache_clear()


def _clear_forecast_cache_everywhere() -> None:
    _FORECAST_CACHE.clear()
    repo = getattr(app.state, "repository", None)
    if repo is not None and hasattr(repo, "forecast_cache"):
        repo.forecast_cache.clear()


def _run_forecasts(*scenarios: str, n_simulations: int = 100) -> None:
    response = client.post(
        "/api/v1/forecasts/run",
        json={
            "scenarios": list(scenarios),
            "model_version": "latest",
            "n_simulations": n_simulations,
        },
    )
    assert response.status_code == 200, response.text


@pytest.fixture(autouse=True)
def reset_app_repository():
    _FORECAST_CACHE.clear()
    app.state.repository = InMemoryRepository(
        cases=list(CASES),
        validations=dict(VALIDATIONS),
        inferences=list(INFERENCES),
        alerts=list(ALERTS),
    )
    app.state.db_health = {
        "storage": "memory",
        "database_configured": False,
        "database_reachable": None,
        "detail": "test repository",
    }
    app.state.jobs = None
    yield
    _FORECAST_CACHE.clear()


def test_geo_outbreak_metapop_risk_source(enable_metapop):
    r = client.get("/api/v1/geo/outbreak?risk_model=metapop&metapop_runs=8")
    assert r.status_code == 200
    assert r.json()["metadata"].get("risk_source") == "metapop_monte_carlo"


def test_geo_outbreak_metapop_disabled_coerces_to_legacy():
    """EOSP_METAPOP_ENABLED defaults false — metapop product surface is deprecated."""
    r = client.get("/api/v1/geo/outbreak?risk_model=metapop")
    assert r.status_code == 200
    md = r.json()["metadata"]
    assert md.get("risk_source") != "metapop_monte_carlo"
    assert "OpenSky" in md.get("risk_heatmap_explanation", "")


def test_geo_outbreak_metapop_includes_sidecar_metadata(tmp_path):
    from eosp.core.seed_data import CASES
    from eosp.services.geo import build_outbreak_geo

    data_dir = Path(__file__).resolve().parents[1] / "app" / "eosp" / "data"
    mob = tmp_path / "mobility_minimal.json"
    copyfile(data_dir / "mobility_weekly_skeleton.json", mob)
    copyfile(
        Path(__file__).resolve().parent / "fixtures" / "mobility_minimal.meta.json",
        tmp_path / "mobility_minimal.meta.json",
    )

    out = build_outbreak_geo(
        p_transmit=0.1,
        cases=list(CASES),
        risk_model="metapop",
        metapop_n_runs=4,
        metapop_mobility_path=mob,
    )
    md = out["metadata"]
    assert md["risk_source"] == "metapop_monte_carlo"
    assert md["mobility_bundle_version"] == "mobility_minimal.json"
    assert md["mobility_horizon_days"] == 14
    assert md["mobility_source"] == "fixture OpenFlights"
    assert md["mobility_license_note"] == "Test fixture only."


def test_geo_outbreak_metapop_serves_run_snapshot_cache_when_current(monkeypatch, enable_metapop):
    repo = app.state.repository
    inf = repo.latest_inference()
    repo.cache_geo_outbreak(
        inf.version,
        {
            "ship": {"lat": 1, "lng": 2, "name": "Test", "status": "x"},
            "confirmed_cases": [],
            "evacuation_flights": [],
            "risk_heatmap": [{"airport_iata": "TST", "lat": 0, "lng": 0, "risk_score": 3.14}],
            "metadata": {"risk_source": "metapop_monte_carlo", "metapop_n_runs": 8},
        },
    )

    def boom(**kwargs):
        raise AssertionError("build_outbreak_geo should not run when snapshot matches")

    monkeypatch.setattr("eosp.api.routes.build_outbreak_geo", boom)
    r = client.get("/api/v1/geo/outbreak?risk_model=metapop")
    assert r.status_code == 200
    data = r.json()
    assert data["risk_heatmap"][0]["airport_iata"] == "TST"
    assert data["metadata"]["metapop_n_runs"] == 8


def test_health_check():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data.get("storage") in ("memory", "postgres")
    assert "database_configured" in data
    assert "database_reachable" in data


def test_case_summary_matches_seed_outbreak():
    response = client.get("/api/v1/cases/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_confirmed"] == 3
    assert payload["total_suspected"] == 5
    assert payload["total_deaths"] == 3
    assert payload["data_sources"]["WHO_DON"] == 4
    assert "external_feed_last_checked_at" in payload


def test_forecast_includes_credible_intervals():
    _run_forecasts("baseline")
    response = client.get("/api/v1/forecasts/baseline")
    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario"] == "baseline"
    assert len(payload["forecast"]) == 14
    final_cases = payload["forecast"][-1]["cases_cumulative"]
    assert final_cases["ci_95_lower"] <= final_cases["median"] <= final_cases["ci_95_upper"]
    assert 3 <= final_cases["median"] <= 60
    assert payload["metadata"]["freshness_status"] == "current"
    assert payload["metadata"]["posterior_version"] == payload["metadata"]["model_version"]


def test_forecast_metadata_carries_inferred_parameters():
    _run_forecasts("baseline")
    response = client.get("/api/v1/forecasts/baseline")
    payload = response.json()
    parameter_values = payload["metadata"]["parameter_values"]
    assert parameter_values["p_transmit_mean"] > 0
    assert parameter_values["contacts_daily_mean"] > 0
    assert parameter_values["incubation_mean"] > 0


def test_forecast_metadata_geo_buckets_from_abm():
    _run_forecasts("baseline")
    response = client.get("/api/v1/forecasts/baseline")
    assert response.status_code == 200
    geo = response.json()["metadata"].get("geo_forecast") or {}
    assert geo.get("metric") == "cumulative_infected"
    assert "ship" in (geo.get("bucket_order") or [])
    assert len(geo.get("by_day") or []) == 14
    last = geo["by_day"][-1]
    assert "buckets" in last and "ship" in last["buckets"]


def test_scenario_comparison_reports_baseline_delta():
    _run_forecasts("baseline", "quarantine_immediate")
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
    assert "No simulation results available" in response.json()["detail"]


def test_scenario_compare_partial_when_scenario_missing_from_cache():
    _run_forecasts("baseline")
    repo = getattr(app.state, "repository", None)
    if repo is None or not hasattr(repo, "forecast_cache"):
        pytest.skip("partial compare test requires in-memory forecast_cache")
    repo.forecast_cache.pop("quarantine_immediate", None)
    response = client.get("/api/v1/scenarios/compare?scenarios=baseline,quarantine_immediate")
    assert response.status_code == 200
    body = response.json()
    names = [s["name"] for s in body["scenarios"]]
    assert names == ["baseline"]
    assert "quarantine_immediate" in body.get("scenarios_unavailable", [])


def test_scenario_compare_returns_503_when_cache_empty():
    _clear_forecast_cache_everywhere()
    response = client.get("/api/v1/scenarios/compare?scenarios=baseline,quarantine_immediate")
    assert response.status_code == 503
