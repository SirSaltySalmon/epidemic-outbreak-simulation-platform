from pathlib import Path
from shutil import copyfile

from fastapi.testclient import TestClient
import pytest

from eosp.api.deps import claims_console_access, require_clerk_session
from eosp.core.clerk_console import fetch_clerk_public_metadata
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
def reset_app_repository(monkeypatch):
    """Tests bypass Clerk JWT; host `.env` may still set ``EOSP_CLERK_*``."""
    monkeypatch.setenv("EOSP_CLERK_FRONTEND_API", "")
    monkeypatch.delenv("EOSP_CLERK_FRONTEND_API", raising=False)
    # Host `.env` may set a secret; `delenv` does not override file-backed values — force empty.
    monkeypatch.setenv("EOSP_CLERK_SECRET_KEY", "")
    get_settings.cache_clear()
    app.dependency_overrides[require_clerk_session] = lambda: None
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
    app.dependency_overrides.pop(require_clerk_session, None)
    _FORECAST_CACHE.clear()


def test_claims_console_access_detects_metadata_and_claim():
    assert claims_console_access({"public_metadata": {"console_access": True}})
    assert not claims_console_access({"public_metadata": {"console_access": False}})
    assert claims_console_access({"console_access": True})
    assert not claims_console_access({"public_metadata": {}})
    assert not claims_console_access({"sub": "x"})


def test_fetch_clerk_public_metadata_sends_user_agent(monkeypatch):
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"public_metadata":{"console_access":true}}'

    def fake_urlopen(req, timeout):
        captured["user_agent"] = req.headers.get("User-agent")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    pm = fetch_clerk_public_metadata("user_real", "sk_test_x")

    assert pm == {"console_access": True}
    assert captured["user_agent"]
    assert "Python-urllib" not in captured["user_agent"]


def test_public_config_sets_public_cache_header():
    r = client.get("/api/v1/public-config")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "public" in cc
    assert "max-age=" in cc


def test_reference_countries_allows_public_cache():
    r = client.get("/api/v1/reference/countries")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "public" in cc
    assert "max-age=" in cc


def test_cases_summary_allows_public_cache():
    r = client.get("/api/v1/cases/summary")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "public" in cc
    assert "max-age=" in cc


def test_dashboard_bootstrap_allows_public_cache():
    r = client.get("/api/v1/dashboard/bootstrap")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "public" in cc
    assert "max-age=" in cc


def test_dashboard_bootstrap_matches_disaggregated_endpoints():
    b = client.get("/api/v1/dashboard/bootstrap?version_limit=2").json()
    summary = client.get("/api/v1/cases/summary").json()
    assert b["summary"]["total_confirmed"] == summary["total_confirmed"]
    assert b["summary"]["total_deaths"] == summary["total_deaths"]
    cases = client.get("/api/v1/cases").json()
    assert len(b["cases"]) == len(cases)
    geo = client.get("/api/v1/geo/outbreak").json()
    assert b["geo"]["ship"]["name"] == geo["ship"]["name"]
    versions = client.get("/api/v1/inference/versions?limit=2").json()
    assert len(b["versions"]["versions"]) == len(versions["versions"])


def test_dashboard_bootstrap_unknown_scenario_returns_error_field():
    r = client.get("/api/v1/dashboard/bootstrap?scenarios=baseline,not_a_real_scenario")
    assert r.status_code == 200
    body = r.json()
    assert body["scenarios"] is None
    assert "not_a_real_scenario" in body["errors"]["scenarios"]


def test_console_access_denied_for_patch_when_claims_lack_flag():
    app.dependency_overrides[require_clerk_session] = lambda: {"sub": "user_test", "public_metadata": {}}
    try:
        cases = client.get("/api/v1/cases").json()
        cid = cases[0]["case_id"]
        r = client.patch(
            f"/api/v1/cases/{cid}",
            json={"patient_identifier": "nope", "updated_reason": "t"},
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "Console access required"
    finally:
        app.dependency_overrides[require_clerk_session] = lambda: None


def test_console_access_denied_for_forecast_run():
    app.dependency_overrides[require_clerk_session] = lambda: {"sub": "u", "public_metadata": {}}
    try:
        r = client.post(
            "/api/v1/forecasts/run",
            json={
                "scenarios": ["baseline"],
                "model_version": "latest",
                "n_simulations": 100,
            },
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "Console access required"
    finally:
        app.dependency_overrides[require_clerk_session] = lambda: None


def test_console_access_allows_patch_when_metadata_true():
    app.dependency_overrides[require_clerk_session] = lambda: {
        "sub": "u",
        "public_metadata": {"console_access": True},
    }
    try:
        cases = client.get("/api/v1/cases").json()
        cid = cases[0]["case_id"]
        r = client.patch(
            f"/api/v1/cases/{cid}",
            json={"patient_identifier": "console_ok", "updated_reason": "t"},
        )
        assert r.status_code == 200
        assert r.json()["case"]["patient_identifier"] == "console_ok"
    finally:
        app.dependency_overrides[require_clerk_session] = lambda: None


def test_console_access_allows_patch_via_clerk_api_when_jwt_omits_metadata(monkeypatch):
    """Default Clerk JWTs omit public_metadata; secret key + Backend API supplies it."""

    def fake_fetch(uid: str, secret: str, api_version: str = "2025-04-10"):
        assert uid == "user_real"
        assert secret == "sk_test_x"
        assert api_version == "2025-04-10"
        return {"console_access": True}

    # Patch name as bound in ``eosp.api.deps`` (import copies the reference).
    monkeypatch.setattr("eosp.api.deps.fetch_clerk_public_metadata", fake_fetch)
    monkeypatch.setenv("EOSP_CLERK_SECRET_KEY", "sk_test_x")
    get_settings.cache_clear()
    app.dependency_overrides[require_clerk_session] = lambda: {"sub": "user_real"}
    try:
        cases = client.get("/api/v1/cases").json()
        cid = cases[0]["case_id"]
        r = client.patch(
            f"/api/v1/cases/{cid}",
            json={"patient_identifier": "via_api", "updated_reason": "t"},
        )
        assert r.status_code == 200
        assert r.json()["case"]["patient_identifier"] == "via_api"
    finally:
        monkeypatch.delenv("EOSP_CLERK_SECRET_KEY", raising=False)
        get_settings.cache_clear()
        app.dependency_overrides[require_clerk_session] = lambda: None


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


def test_geo_outbreak_abm_geo_fallback_without_cached_forecast():
    r = client.get("/api/v1/geo/outbreak?risk_model=abm_geo")
    assert r.status_code == 200
    metadata = r.json()["metadata"]
    assert metadata.get("abm_geo_fallback_reason") or metadata.get("risk_source") == "legacy_opensky_fallback"


def test_geo_outbreak_abm_geo_uses_forecast_when_cached():
    _run_forecasts("baseline")
    r = client.get("/api/v1/geo/outbreak?risk_model=abm_geo")
    assert r.status_code == 200
    payload = r.json()
    assert payload["metadata"].get("risk_source") == "abm_geo_forecast"
    assert payload["risk_heatmap"]


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


def test_reference_countries_returns_sorted_codes():
    r = client.get("/api/v1/reference/countries")
    assert r.status_code == 200
    data = r.json()
    assert "countries" in data
    codes = [c["code"] for c in data["countries"]]
    assert codes == sorted(codes)
    assert all(len(c) == 2 for c in codes)
    assert "ES" in codes


def test_reference_airports_filtered_by_country():
    r = client.get("/api/v1/reference/airports?country=ES")
    assert r.status_code == 200
    for a in r.json()["airports"]:
        assert a["country"] == "ES"


def test_validation_accepts_countries_from_reference_geo():
    """Countries in airport_coords (e.g. DE) pass geographic_plausibility."""

    from datetime import date
    from uuid import uuid4

    from eosp.core.models import CaseCreate, CaseStatus, LabResult, ObservationKind
    from eosp.core.repository import InMemoryRepository

    repo = InMemoryRepository(cases=[], validations={}, inferences=[], alerts=[])
    payload = CaseCreate(
        case_id=uuid4(),
        patient_identifier="geo_test",
        symptom_onset_date=date(2026, 5, 1),
        location_country="DE",
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.NOT_TESTED,
        data_source="Manual_Form",
        observation_kind=ObservationKind.INDIVIDUAL,
    )
    _, val = repo.create_case(payload)
    assert val.checks_passed["geographic_plausibility"] is True


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


def test_baseline_forecast_stores_legacy_opensky_geo_snapshot():
    _run_forecasts("baseline")
    payload = client.get("/api/v1/forecasts/baseline").json()
    snap = payload["metadata"].get("legacy_opensky_geo")
    assert isinstance(snap, dict)
    assert "risk_heatmap" in snap and "metadata" in snap
    assert isinstance(snap["risk_heatmap"], list)


def test_geo_legacy_uses_cached_rings_after_baseline_run():
    _run_forecasts("baseline")
    geo = client.get("/api/v1/geo/outbreak?risk_model=legacy").json()
    assert geo["metadata"].get("risk_source") in (
        "legacy_opensky_cached_simulation",
        "legacy_opensky_live_simulation",
    )


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


def test_get_case_returns_seed_row():
    cases = client.get("/api/v1/cases").json()
    cid = cases[0]["case_id"]
    r = client.get(f"/api/v1/cases/{cid}")
    assert r.status_code == 200
    assert r.json()["case_id"] == cid


def test_patch_case_updates_field():
    cases = client.get("/api/v1/cases").json()
    cid = cases[0]["case_id"]
    r = client.patch(
        f"/api/v1/cases/{cid}",
        json={"patient_identifier": "patched_label", "updated_reason": "pytest_patch"},
    )
    assert r.status_code == 200
    assert r.json()["case"]["patient_identifier"] == "patched_label"
    again = client.get(f"/api/v1/cases/{cid}")
    assert again.json()["patient_identifier"] == "patched_label"


def test_delete_case_removes_from_list():
    from datetime import date
    from uuid import uuid4

    from eosp.core.models import CaseCreate, CaseStatus, LabResult, ObservationKind

    create = client.post(
        "/api/v1/cases",
        json={
            "case_id": str(uuid4()),
            "patient_identifier": "to_delete",
            "symptom_onset_date": "2026-05-02",
            "location_country": "ES",
            "confirmed_or_suspected": "suspected",
            "lab_test_result": "not_tested",
            "data_source": "Manual_Form",
            "observation_kind": "individual",
        },
    )
    assert create.status_code == 201
    cid = create.json()["case"]["case_id"]
    del_r = client.delete(f"/api/v1/cases/{cid}")
    assert del_r.status_code == 204
    assert client.get(f"/api/v1/cases/{cid}").status_code == 404


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
