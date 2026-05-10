from datetime import date, timedelta

from fastapi.testclient import TestClient
import pytest

from eosp.api.deps import claims_console_access, require_clerk_session
from eosp.core.clerk_console import fetch_clerk_public_metadata
from eosp.core.models import ForecastPoint, ForecastResponse
from eosp.core.repository import InMemoryRepository
from eosp.core.seed_data import ALERTS, CASES, INFERENCES, VALIDATIONS
from eosp.core.settings import get_settings
from eosp.main import app


client = TestClient(app)


def _geo_forecast_for_tests() -> dict:
    hubs = ["ZA_JNB", "QA_DOH", "CH_ZRH", "NL_AMS"]
    by_day = []
    for day in range(1, 15):
        buckets = {}
        for code in hubs:
            base_ci = 3.0 + day * 1.2
            base_peak = 2.0 + day * 0.15
            buckets[code] = {
                "cumulative_infected": {
                    "median": base_ci,
                    "ci_95_lower": base_ci * 0.85,
                    "ci_95_upper": base_ci * 1.15,
                },
                "infectious_I": {"median": 1.0, "ci_95_lower": 0.5, "ci_95_upper": 2.0},
                "peak_infectious_I": {
                    "median": base_peak,
                    "ci_95_lower": base_peak * 0.8,
                    "ci_95_upper": base_peak * 1.2,
                },
            }
        by_day.append({"day": day, "buckets": buckets})
    return {
        "metric": "cumulative_infected",
        "bucket_order": list(hubs),
        "by_day": by_day,
    }


def _replay_geo_for_tests(start: date) -> dict:
    """Minimal `eosp_geo_replay_1` payload for bootstrap / map replay QA."""

    d0 = start.isoformat()
    d1 = (start + timedelta(days=1)).isoformat()
    return {
        "schema_version": "eosp_geo_replay_1",
        "anchor_date": start.isoformat(),
        "days": [
            {"date": d0, "airports": {"JNB": {"A_median": 1.2, "C_median": 10.0}}},
            {
                "date": d1,
                "airports": {
                    "JNB": {"A_median": 2.5, "C_median": 25.0},
                    "AMS": {"A_median": 0.8, "C_median": 5.0},
                },
            },
        ],
    }


def _forecast_series(start: date, n_days: int, final_cumulative_median: float, final_deaths_median: float):
    forecast = []
    denom = max(n_days - 1, 1)
    for d in range(n_days):
        day = d + 1
        t = d / denom
        cum = 3.0 + (final_cumulative_median - 3.0) * t
        prev_cum = 3.0 + (final_cumulative_median - 3.0) * ((d - 1) / denom) if d > 0 else 0.0
        new = max(cum - prev_cum, 0.0) if d > 0 else cum
        death = 0.0 + (final_deaths_median - 0.0) * t
        lo_c, hi_c = cum * 0.9, cum * 1.1
        forecast.append(
            ForecastPoint(
                day=day,
                date=start + timedelta(days=d),
                cases_cumulative={"median": cum, "ci_95_lower": lo_c, "ci_95_upper": hi_c},
                cases_new={"median": new, "ci_95_lower": new * 0.9, "ci_95_upper": new * 1.1},
                deaths_cumulative={"median": death, "ci_95_lower": death * 0.9, "ci_95_upper": death * 1.1},
            )
        )
    return forecast


def _stub_forecast_response(scenario: str, *, final_cases: float, n_sim: int = 100) -> ForecastResponse:
    inf = INFERENCES[0]
    inf_ver = inf.version
    start = date(2026, 4, 25)
    return ForecastResponse(
        scenario=scenario,
        forecast=_forecast_series(start, 14, final_cases, final_cases * 0.05),
        metadata={
            "model_version": inf_ver,
            "posterior_version": inf_ver,
            "n_simulations": n_sim,
            "ensemble_spec_hash": "testhash",
            "freshness_status": "current",
            "horizon_days": 14,
            "anchor_date": start.isoformat(),
            "engine": "hub_timeline_monte_carlo",
            "geo_forecast": _geo_forecast_for_tests(),
            "replay_geo": _replay_geo_for_tests(start),
            "parameter_values": {
                "p_transmit_mean": inf.parameters["p_transmit"].mean,
                "contacts_daily_mean": inf.parameters["contacts_daily"].mean,
                "incubation_mean": inf.parameters["incubation"].mean,
            },
        },
    )


def _seed_cached_forecasts(*scenarios: str) -> None:
    """Populate the in-memory repository with stub forecasts (simulator removed)."""

    repo = app.state.repository
    if not hasattr(repo, "forecast_cache"):
        raise AssertionError("test repository must expose forecast_cache")
    presets = {
        "baseline": _stub_forecast_response("baseline", final_cases=45.0),
        "terminal_distancing": _stub_forecast_response("terminal_distancing", final_cases=30.0),
    }
    for name in scenarios:
        if name not in presets:
            raise AssertionError(f"add stub for scenario {name!r} in test presets")
        repo.forecast_cache[name] = presets[name]


def _clear_forecast_cache_everywhere() -> None:
    repo = getattr(app.state, "repository", None)
    if repo is not None and hasattr(repo, "forecast_cache"):
        repo.forecast_cache.clear()


@pytest.fixture(autouse=True)
def reset_app_repository(monkeypatch):
    """Tests bypass Clerk JWT; host `.env` may still set ``EOSP_CLERK_*``."""
    monkeypatch.setenv("EOSP_CLERK_FRONTEND_API", "")
    monkeypatch.delenv("EOSP_CLERK_FRONTEND_API", raising=False)
    # Host `.env` may set a secret; `delenv` does not override file-backed values — force empty.
    monkeypatch.setenv("EOSP_CLERK_SECRET_KEY", "")
    get_settings.cache_clear()
    app.dependency_overrides[require_clerk_session] = lambda: None
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


def test_forecast_run_request_accepts_single_simulation_preview():
    from eosp.core.models import ForecastRunRequest

    assert ForecastRunRequest(n_simulations=1).n_simulations == 1


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


def test_geo_outbreak_without_cached_forecast_shows_pending_layer():
    r = client.get("/api/v1/geo/outbreak")
    assert r.status_code == 200
    md = r.json()["metadata"]
    assert md.get("risk_source") == "abm_geo_unavailable"


def test_geo_outbreak_uses_forecast_when_cached():
    _seed_cached_forecasts("baseline")
    r = client.get("/api/v1/geo/outbreak?risk_model=abm_geo")
    assert r.status_code == 200
    payload = r.json()
    assert payload["metadata"].get("risk_source") == "abm_geo_forecast"
    assert payload["risk_heatmap"]


def test_geo_bundle_endpoint_returns_schema_version():
    r = client.get("/api/v1/geo/bundle")
    assert r.status_code == 200
    data = r.json()
    assert data.get("schema_version") == "1"
    assert "simulation" in data and "layers" in data["simulation"]


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
    with_coords = [a for a in r.json()["airports"] if a.get("lat") is not None]
    assert with_coords
    assert isinstance(with_coords[0]["lat"], (int, float))
    assert isinstance(with_coords[0]["lng"], (int, float))


def test_dashboard_bootstrap_baseline_has_replay_geo_metadata():
    _seed_cached_forecasts("baseline")
    b = client.get("/api/v1/dashboard/bootstrap").json()
    fb = b["forecast_baseline"]
    assert fb is not None
    meta = fb["metadata"]
    assert meta.get("horizon_days") == 14
    rg = meta.get("replay_geo") or {}
    assert rg.get("schema_version") == "eosp_geo_replay_1"
    assert len(rg.get("days") or []) >= 1
    assert "airports" in rg["days"][0]


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


def test_baseline_forecast_has_geo_forecast_not_opensky_snapshot():
    _seed_cached_forecasts("baseline")
    payload = client.get("/api/v1/forecasts/baseline").json()
    assert "legacy_opensky_geo" not in payload["metadata"]
    geo = payload["metadata"].get("geo_forecast") or {}
    assert geo.get("metric") == "cumulative_infected"


def test_geo_after_baseline_run_uses_abm_kernel():
    _seed_cached_forecasts("baseline")
    geo = client.get("/api/v1/geo/outbreak?risk_model=legacy").json()
    assert geo["metadata"].get("risk_source") == "abm_geo_forecast"


def test_forecast_includes_credible_intervals():
    _seed_cached_forecasts("baseline")
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
    _seed_cached_forecasts("baseline")
    response = client.get("/api/v1/forecasts/baseline")
    payload = response.json()
    parameter_values = payload["metadata"]["parameter_values"]
    assert parameter_values["p_transmit_mean"] > 0
    assert parameter_values["contacts_daily_mean"] > 0
    assert parameter_values["incubation_mean"] > 0


def test_forecast_metadata_geo_buckets_from_abm():
    _seed_cached_forecasts("baseline")
    response = client.get("/api/v1/forecasts/baseline")
    assert response.status_code == 200
    geo = response.json()["metadata"].get("geo_forecast") or {}
    assert geo.get("metric") == "cumulative_infected"
    order = geo.get("bucket_order") or []
    hubs = {"JNB", "DOH", "ZRH", "AMS"}
    assert any(any(h in str(b).upper() for h in hubs) for b in order)
    assert len(geo.get("by_day") or []) == 14
    last = geo["by_day"][-1]
    assert "buckets" in last and any(any(h in str(k).upper() for h in hubs) for k in (last["buckets"] or {}))


def test_scenario_comparison_reports_baseline_delta():
    _seed_cached_forecasts("baseline", "terminal_distancing")
    response = client.get("/api/v1/scenarios/compare?scenarios=baseline,terminal_distancing")
    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    assert scenarios[0]["vs_baseline"] is None
    assert scenarios[1]["vs_baseline"]["case_change_pct"] <= 0


def test_dashboard_bootstrap_includes_scenario_catalog():
    response = client.get("/api/v1/dashboard/bootstrap")
    assert response.status_code == 200
    body = response.json()
    cat = body.get("scenario_catalog")
    assert isinstance(cat, list) and len(cat) == 4
    ids = [x["id"] for x in cat]
    assert ids == [
        "baseline",
        "terminal_distancing",
        "reduced_travel_connectivity",
        "enhanced_case_isolation",
    ]
    for row in cat:
        assert row.get("public_label")
        assert "technical_explanation" in row


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
            "location_country": "QA",
            "location_airport_code": "DOH",
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


def test_run_forecast_stub_engine_returns_200(monkeypatch):
    from uuid import uuid4

    from eosp.core.models import ForecastRunItem

    def fake_engine(scenario, inference, n_simulations, **kwargs):
        fr = _stub_forecast_response(scenario, final_cases=12.0, n_sim=n_simulations)
        item = ForecastRunItem(
            forecast_id=uuid4(),
            scenario=scenario,
            model_version=inference.version,
            n_simulations=n_simulations,
            execution_time_seconds=0.01,
            cases_day_14={k: float(v) for k, v in fr.forecast[-1].cases_cumulative.items()},
            deaths_day_14={k: float(v) for k, v in fr.forecast[-1].deaths_cumulative.items()},
        )
        return item, fr

    monkeypatch.setattr("eosp.api.routes.run_forecast_engine", fake_engine)
    response = client.post(
        "/api/v1/forecasts/run",
        json={
            "scenarios": ["baseline", "terminal_distancing"],
            "model_version": "latest",
            "n_simulations": 250,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_status"] == "completed"
    assert len(body["forecasts"]) == 2

    runs_response = client.get("/api/v1/forecast-runs?limit=5")
    assert runs_response.status_code == 200


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
    _seed_cached_forecasts("baseline")
    repo = getattr(app.state, "repository", None)
    if repo is None or not hasattr(repo, "forecast_cache"):
        pytest.skip("partial compare test requires in-memory forecast_cache")
    repo.forecast_cache.pop("terminal_distancing", None)
    response = client.get("/api/v1/scenarios/compare?scenarios=baseline,terminal_distancing")
    assert response.status_code == 200
    body = response.json()
    names = [s["name"] for s in body["scenarios"]]
    assert names == ["baseline"]
    assert "terminal_distancing" in body.get("scenarios_unavailable", [])


def test_scenario_compare_returns_503_when_cache_empty():
    _clear_forecast_cache_everywhere()
    response = client.get("/api/v1/scenarios/compare?scenarios=baseline,terminal_distancing")
    assert response.status_code == 503
