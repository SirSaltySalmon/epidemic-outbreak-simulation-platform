"""Background job orchestrator for inference + ensemble refreshes.

The :class:`JobManager` owns a small ``ThreadPoolExecutor`` and is attached to
the FastAPI app state via the lifespan handler. Endpoints schedule jobs and
poll their status; the manager mutates the shared repository so subsequent GET
requests serve the freshest forecasts. Refits are debounced (FR-2.2) so a
flurry of new-case ingestions does not spawn N parallel NUTS runs.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from eosp.core.models import CaseRecord, InferenceResult, TriggerType
from eosp.services.ensemble import EnsembleConfig, run_ensemble, seed_state_from_case_counts
from eosp.services.inference import InferenceArtifacts, InferenceConfig, run_inference
from eosp.services.network import ContactNetwork
from eosp.services.scenarios import ScenarioSpec


logger = logging.getLogger(__name__)


JOB_REFIT = "refit"
JOB_ENSEMBLE = "ensemble"
JOB_FULL_REFRESH = "full_refresh"


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    status: str = "pending"
    reason: str = ""
    scheduled_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    progress_events: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=200)
    )

    def push_event(self, event: dict) -> None:
        self.progress_events.append(event)

    def drain_events(self) -> list[dict]:
        events = list(self.progress_events)
        self.progress_events.clear()
        return events

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status,
            "reason": self.reason,
            "scheduled_at": self.scheduled_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "detail": self.detail,
            "error": self.error,
        }


class JobManager:
    def __init__(
        self,
        *,
        repository: Any,
        network: ContactNetwork,
        scenarios: Mapping[str, ScenarioSpec],
        max_workers: int = 2,
        debounce_seconds: float = 30.0,
        ensemble_config: EnsembleConfig | None = None,
        inference_config: InferenceConfig | None = None,
    ) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="eosp-jobs")
        self._repository = repository
        self._network = network
        self._scenarios = dict(scenarios)
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._last_refit_completed_at: float = 0.0
        self._debounce_seconds = float(debounce_seconds)
        self._ensemble_config = ensemble_config or EnsembleConfig()
        self._inference_config = inference_config or InferenceConfig()
        self._refit_in_flight = False

    def schedule_full_refresh(self, *, reason: str = "startup", trigger: TriggerType = TriggerType.MANUAL) -> str:
        record = self._register(JOB_FULL_REFRESH, reason=reason, detail={"trigger": trigger.value})
        self._executor.submit(self._safe_run, record, lambda: self._run_full_refresh(record, trigger))
        return record.job_id

    def schedule_refit(
        self,
        *,
        reason: str,
        trigger: TriggerType = TriggerType.NEW_CASE,
    ) -> str | None:
        with self._lock:
            now = time.monotonic()
            if self._refit_in_flight:
                logger.info("Skipping refit (%s): already in flight", reason)
                return None
            if now - self._last_refit_completed_at < self._debounce_seconds:
                logger.info("Debouncing refit (%s)", reason)
                return None
            self._refit_in_flight = True
        record = self._register(JOB_REFIT, reason=reason, detail={"trigger": trigger.value})
        self._executor.submit(self._safe_run, record, lambda: self._run_full_refresh(record, trigger))
        return record.job_id

    def schedule_ensemble(self, *, scenario_name: str, reason: str = "manual") -> str:
        record = self._register(JOB_ENSEMBLE, reason=reason, detail={"scenario": scenario_name})
        self._executor.submit(self._safe_run, record, lambda: self._run_single_ensemble(record, scenario_name))
        return record.job_id

    def get_status(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def list_recent(self, limit: int = 20) -> list[JobRecord]:
        return sorted(self._jobs.values(), key=lambda record: record.scheduled_at, reverse=True)[:limit]

    def push_event(self, job_id: str, event: dict) -> None:
        record = self._jobs.get(job_id)
        if record is not None:
            record.push_event(event)

    def drain_events(self, job_id: str) -> list[dict]:
        record = self._jobs.get(job_id)
        if record is None:
            return []
        return record.drain_events()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _register(self, job_type: str, *, reason: str, detail: dict[str, Any] | None = None) -> JobRecord:
        record = JobRecord(job_id=uuid.uuid4().hex, job_type=job_type, reason=reason, detail=detail or {})
        self._jobs[record.job_id] = record
        return record

    def _safe_run(self, record: JobRecord, work: Any) -> None:
        record.status = "running"
        record.started_at = datetime.now(UTC)
        try:
            work()
            record.status = "completed"
        except Exception as exc:
            logger.exception("Background job %s (%s) failed", record.job_id, record.job_type)
            record.status = "failed"
            record.error = f"{type(exc).__name__}: {exc}"
        finally:
            record.completed_at = datetime.now(UTC)
            if record.job_type in (JOB_REFIT, JOB_FULL_REFRESH):
                with self._lock:
                    self._refit_in_flight = False
                    self._last_refit_completed_at = time.monotonic()

    def _run_full_refresh(self, record: JobRecord, trigger: TriggerType) -> None:
        cases = self._load_cases()
        if not cases:
            record.detail["skipped"] = "no_cases"
            return
        try:
            artifacts = run_inference(
                cases=cases,
                network=self._network,
                config=self._inference_config,
                trigger=trigger,
            )
        except RuntimeError as exc:
            record.detail["inference_error"] = str(exc)
            logger.warning("Inference unavailable, falling back to existing posterior: %s", exc)
            artifacts = None

        if artifacts is not None:
            self._update_inference(artifacts.result)
            record.detail["inference_version"] = artifacts.result.version
            inference = artifacts.result
            samples = artifacts.posterior_samples
        else:
            inference = self._repository.latest_inference()
            samples = None

        seed = self._build_seed_state(cases)
        for scenario_name, spec in self._scenarios.items():
            response = run_ensemble(
                scenario=spec,
                inference=inference,
                network=self._network,
                seed=seed,
                config=self._ensemble_config,
                posterior_samples=samples,
            )
            self._cache_forecast(scenario_name, response)
        record.detail["scenarios_refreshed"] = list(self._scenarios.keys())

    def _run_single_ensemble(self, record: JobRecord, scenario_name: str) -> None:
        if scenario_name not in self._scenarios:
            raise KeyError(scenario_name)
        cases = self._load_cases()
        seed = self._build_seed_state(cases)
        inference = self._repository.latest_inference()
        response = run_ensemble(
            scenario=self._scenarios[scenario_name],
            inference=inference,
            network=self._network,
            seed=seed,
            config=self._ensemble_config,
        )
        self._cache_forecast(scenario_name, response)
        record.detail["model_version"] = inference.version

    def _load_cases(self) -> list[CaseRecord]:
        try:
            return list(self._repository.list_cases())
        except Exception as exc:
            logger.warning("Failed to load cases for inference: %s", exc)
            return []

    def _build_seed_state(self, cases: list[CaseRecord]) -> Any:
        n_deceased = sum(1 for case in cases if case.death_date is not None)
        n_observed_alive = max(0, len(cases) - n_deceased)
        n_recovered = max(0, n_observed_alive - 2)
        n_active = min(2, n_observed_alive)
        hidden_multiplier = 1.5
        n_hidden_exposed = max(4, int(round(len(cases) * hidden_multiplier)))
        return seed_state_from_case_counts(
            network=self._network,
            n_recent_active=n_active + 1,
            n_recovered=n_recovered,
            n_deceased=n_deceased,
            n_recently_exposed=n_hidden_exposed,
        )

    def _update_inference(self, inference: InferenceResult) -> None:
        if hasattr(self._repository, "update_inference"):
            self._repository.update_inference(inference)
        elif hasattr(self._repository, "inferences"):
            self._repository.inferences.insert(0, inference)

    def _cache_forecast(self, scenario_name: str, forecast: Any) -> None:
        if hasattr(self._repository, "cache_forecast"):
            self._repository.cache_forecast(scenario_name, forecast)
