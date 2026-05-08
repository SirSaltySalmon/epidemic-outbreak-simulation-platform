from datetime import UTC, date, datetime
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any

from eosp.core.models import ForecastPoint, ForecastResponse, InferenceResult, TriggerType
from eosp.core.seed_data import CASES, INFERENCES
from eosp.services.jobs import JobManager
from eosp.services.network import build_default_network
from eosp.services.scenarios import SCENARIO_CONFIG


class _EmptyRepository:
    def list_cases(self):
        return []


def test_schedule_full_refresh_can_be_called_on_manager_instance():
    manager = JobManager(
        repository=_EmptyRepository(),
        network=object(),
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

    def get_geo_outbreak_cache(self, inference_version: str, *, metapop_runs: int | None = None):
        if self.geo_cache is None or self.geo_cache_version != inference_version:
            return None
        if metapop_runs is not None:
            cr = self.geo_cache.get("metadata", {}).get("metapop_n_runs")
            if cr is not None and int(cr) != int(metapop_runs):
                return None
        return self.geo_cache


def _forecast(scenario: str, version: str, n_simulations: int) -> ForecastResponse:
    return ForecastResponse(
        scenario=scenario,
        forecast=[
            ForecastPoint(
                day=1,
                date=date(2026, 5, 7),
                cases_cumulative={"median": 1.0, "ci_95_lower": 1.0, "ci_95_upper": 1.0},
                cases_new={"median": 1.0, "ci_95_lower": 1.0, "ci_95_upper": 1.0},
                deaths_cumulative={"median": 0.0, "ci_95_lower": 0.0, "ci_95_upper": 0.0},
            )
        ],
        metadata={"model_version": version, "n_simulations": n_simulations},
    )


def _wait_for_terminal(manager: JobManager, job_id: str):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        record = manager.get_status(job_id)
        if record is not None and record.status in {"completed", "failed"}:
            return record
        sleep(0.01)
    raise AssertionError("job did not finish")


def test_full_refresh_emits_start_and_final_simulation_progress_for_small_runs(monkeypatch):
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

    def fake_run_ensemble(*, scenario, inference, config, **kwargs):
        return _forecast(scenario.name, inference.version, config.n_simulations)

    monkeypatch.setattr("eosp.services.jobs.run_inference", fake_run_inference)
    monkeypatch.setattr("eosp.services.jobs.run_ensemble", fake_run_ensemble)

    manager = JobManager(
        repository=repo,
        network=build_default_network(),
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
    assert simulation_events[0] == {
        "stage": "simulation",
        "status": "running",
        "scenario": "baseline",
        "trajectories": 0,
        "total": 100,
    }
    assert simulation_events[-1]["status"] == "complete"
    assert simulation_events[-1]["trajectories"] == 100
    assert simulation_events[-1]["total"] == 100


def test_full_refresh_runs_metapop_geo_when_geo_risk_model_is_metapop(monkeypatch):
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

    def fake_run_ensemble(*, scenario, inference, config, **kwargs):
        return _forecast(scenario.name, inference.version, config.n_simulations)

    captured: dict[str, Any] = {}

    def fake_build_outbreak_geo(*, p_transmit, cases, risk_model, metapop_n_runs, **kwargs):
        captured["risk_model"] = risk_model
        captured["metapop_n_runs"] = metapop_n_runs
        captured["p_transmit"] = p_transmit
        cb = kwargs.get("metapop_progress_callback")
        if cb and metapop_n_runs:
            cb({"runs_completed": metapop_n_runs // 2, "total_runs": metapop_n_runs, "elapsed_s": 0.01})
            cb({"runs_completed": metapop_n_runs, "total_runs": metapop_n_runs, "elapsed_s": 0.02})
        return {
            "ship": {},
            "confirmed_cases": [],
            "evacuation_flights": [],
            "risk_heatmap": [],
            "metadata": {"risk_source": "metapop_monte_carlo", "metapop_n_runs": metapop_n_runs},
        }

    monkeypatch.setattr("eosp.services.jobs.run_inference", fake_run_inference)
    monkeypatch.setattr("eosp.services.jobs.run_ensemble", fake_run_ensemble)
    monkeypatch.setattr("eosp.services.jobs.build_outbreak_geo", fake_build_outbreak_geo)
    monkeypatch.setattr(
        "eosp.services.jobs.get_settings",
        lambda: SimpleNamespace(geo_risk_model="metapop", metapop_enabled=True),
    )

    manager = JobManager(
        repository=repo,
        network=build_default_network(),
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

    assert record.status == "completed"
    assert captured.get("risk_model") == "metapop"
    assert captured.get("metapop_n_runs") == 64
    metapop_events = [e for e in record.drain_events() if e.get("stage") == "metapop_geo"]
    assert metapop_events and metapop_events[-1].get("status") == "complete"
    assert repo.geo_cache is not None
    assert repo.geo_cache_version == inference.version
    assert record.detail.get("metapop_geo", {}).get("status") == "complete"
    progress_events = [e for e in metapop_events if e.get("status") == "running" and e.get("runs_completed")]
    assert progress_events, "expected metapop progress SSE events with runs_completed"
    assert progress_events[-1].get("runs_completed") == 64


def test_full_refresh_fails_instead_of_falling_back_to_stored_posterior(monkeypatch):
    repo = _Repository()
    repo.inferences.append(INFERENCES[0])

    def fake_run_inference(**kwargs):
        raise RuntimeError("sampler unavailable")

    def fake_run_ensemble(**kwargs):
        raise AssertionError("ensemble should not run without a fresh inference")

    monkeypatch.setattr("eosp.services.jobs.run_inference", fake_run_inference)
    monkeypatch.setattr("eosp.services.jobs.run_ensemble", fake_run_ensemble)

    manager = JobManager(
        repository=repo,
        network=build_default_network(),
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
