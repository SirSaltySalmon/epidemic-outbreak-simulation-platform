import pytest
from fastapi.testclient import TestClient

from eosp.core.settings import get_settings
from eosp.main import create_app


@pytest.fixture
def app_without_database(monkeypatch):
    monkeypatch.setenv("EOSP_DATABASE_URL", "")
    get_settings.cache_clear()
    try:
        yield create_app()
    finally:
        get_settings.cache_clear()


def test_unconfigured_startup_uses_empty_repository_not_seeded_results(app_without_database):
    repo = app_without_database.state.repository

    assert repo.list_cases() == []
    assert repo.get_inference("latest") is None


def test_visitor_forecast_reports_no_simulation_result_when_none_exists(app_without_database):
    with TestClient(app_without_database) as client:
        response = client.get("/api/v1/forecasts/baseline")
        versions = client.get("/api/v1/inference/versions")

    assert response.status_code == 503
    assert "No simulation results available" in response.json()["detail"]
    assert versions.status_code == 200
    assert versions.json() == {"versions": []}
