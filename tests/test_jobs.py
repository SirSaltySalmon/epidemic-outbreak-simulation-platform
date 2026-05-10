from datetime import UTC, datetime
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any

from eosp.core.models import ForecastResponse, InferenceResult, TriggerType
from eosp.core.seed_data import CASES, INFERENCES
from eosp.services.jobs import JobManager
from eosp.services.scenarios import SCENARIO_CONFIG


class _EmptyRepository:
    def list_cases(self):
        return []


def test_schedule_full_refresh_can_be_called_on_manager_instance():
    manager = JobManager(
        repository=_EmptyRepository(),
        scenarios=SCENARIO_CONFIG,
        max_workers=1,
    )
    try:
        job_id = manager.schedule_full_refresh(
            reason="manual",
            scenarios=["baseline"],
            n_simulations=100,
        )

        assert isinstance(job_id, str)
        assert manager.get_status(job_id) is not None
    finally:
        manager.shutdown()


class _Repository:
    def __init__(self):
        self.cases = list(CASES)
        self.inferences: list[InferenceResult] = []
        self.forecasts: dict[str, ForecastResponse] = {}
        self.geo_cache: dict[str, Any] | None = None
        self.geo_cache_version: str | None = None

    def list_cases(self):
        return list(self.cases)

    def latest_inference(self):
        if not self.inferences:
            raise LookupError("No inference traces available")
        return self.inferences[0]

    def update_inference(self, inference):
        self.inferences.insert(0, inference)

    def cache_forecast(self, scenario, forecast):
        self.forecasts[scenario] = forecast

    def cache_geo_outbreak(self, inference_version: str, payload: dict[str, Any]) -> None:
        self.geo_cache_version = inference_version
        self.geo_cache = payload

    def get_geo_outbreak_cache(self, inference_version: str):
        if self.geo_cache is None or self.geo_cache_version != inference_version:
            return None
        return self.geo_cache


def _wait_for_terminal(manager: JobManager, job_id: str):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        record = manager.get_status(job_id)
        if record is not None and record.status in {"completed", "failed"}:
            return record
        sleep(0.01)
    raise AssertionError("job did not finish")


def test_full_refresh_records_simulation_skipped_after_inference(monkeypatch):
    repo = _Repository()
    inference = INFERENCES[0].model_copy(
        update={
            "version": "v1.0.0-20260508T120000Z",
            "timestamp": datetime(2026, 5, 8, 12, tzinfo=UTC),
            "trigger": TriggerType.MANUAL,
        }
    )

    def fake_run_inference(**kwargs):
        return SimpleNamespace(result=inference, posterior_samples={}, netcdf_path=None)

    monkeypatch.setattr("eosp.services.inference.run_inference", fake_run_inference)

    manager = JobManager(
        repository=repo,
        scenarios=SCENARIO_CONFIG,
        max_workers=1,
    )
    try:
        job_id = manager.schedule_full_refresh(
            reason="manual",
            scenarios=["baseline"],
            n_simulations=100,
        )
        assert isinstance(job_id, str)
        record = _wait_for_terminal(manager, job_id)
        events = record.drain_events()
    finally:
        manager.shutdown()

    simulation_events = [event for event in events if event.get("stage") == "simulation"]
    assert len(simulation_events) == 1
    assert simulation_events[0]["status"] == "skipped"
    assert simulation_events[0].get("reason") == "abm_removed"
    assert repo.forecasts == {}


def test_full_refresh_fails_instead_of_falling_back_to_stored_posterior(monkeypatch):
    repo = _Repository()
    repo.inferences.append(INFERENCES[0])

    def fake_run_inference(**kwargs):
        raise RuntimeError("sampler unavailable")

    monkeypatch.setattr("eosp.services.inference.run_inference", fake_run_inference)

    manager = JobManager(
        repository=repo,
        scenarios=SCENARIO_CONFIG,
        max_workers=1,
    )
    try:
        job_id = manager.schedule_full_refresh(
            reason="manual",
            scenarios=["baseline"],
            n_simulations=100,
        )
        assert isinstance(job_id, str)
        record = _wait_for_terminal(manager, job_id)
    finally:
        manager.shutdown()

    assert record.status == "failed"
    assert "sampler unavailable" in (record.error or "")
    assert repo.forecasts == {}
