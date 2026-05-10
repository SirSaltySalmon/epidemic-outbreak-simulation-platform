"""Background job orchestrator for inference refreshes.

The :class:`JobManager` owns a small ``ThreadPoolExecutor`` and is attached to
the FastAPI app state via the lifespan handler. After NumPyro inference, full
refresh runs the hub timeline Monte Carlo for configured scenarios and
repopulates forecast caches (see ``docs/superpowers/specs/2026-05-10-eosp-hub-timeline-simulator-design.md``).
"""

from __future__ import annotations

import collections
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from eosp.core.case_statistics import (
    total_cohort_persons,
    validation_quality_weighted_mean,
)
from eosp.core.compute_config import EnsembleConfig, InferenceConfig
from eosp.core.models import CaseRecord, InferenceResult, TriggerType
from eosp.core.settings import get_settings
from eosp.services.network import build_default_network
from eosp.services.scenarios import ScenarioSpec


logger = logging.getLogger(__name__)


JOB_REFIT = "refit"
JOB_ENSEMBLE = "ensemble"
JOB_FULL_REFRESH = "full_refresh"

_PROGRESS_EVENT_BUFFER_SIZE = 3500  # hub-timeline SSE: per-day × trajectory events per scenario


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
        default_factory=lambda: collections.deque(maxlen=_PROGRESS_EVENT_BUFFER_SIZE)
    )

    def push_event(self, event: dict[str, Any]) -> None:
        self.progress_events.append(event)

    def drain_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self.progress_events.popleft())
            except IndexError:
                break
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
        scenarios: Mapping[str, ScenarioSpec],
        max_workers: int = 2,
        debounce_seconds: float = 30.0,
        ensemble_config: EnsembleConfig | None = None,
        inference_config: InferenceConfig | None = None,
    ) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="eosp-jobs")
        self._repository = repository
        self._scenarios = dict(scenarios)
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._last_refit_completed_at: float = 0.0
        self._debounce_seconds = float(debounce_seconds)
        self._ensemble_config = ensemble_config or EnsembleConfig()
        self._inference_config = inference_config or InferenceConfig()
        self._refit_in_flight = False
        self._pipeline_job_id: str | None = None

    def get_active_pipeline_job(self) -> JobRecord | None:
        with self._lock:
            jid = self._pipeline_job_id
            if not jid:
                return None
            rec = self._jobs.get(jid)
            if rec is not None and rec.status in ("pending", "running"):
                return rec
            return None

    def _pipeline_busy_unlocked(self) -> bool:
        jid = self._pipeline_job_id
        if not jid:
            return False
        rec = self._jobs.get(jid)
        return rec is not None and rec.status in ("pending", "running")

    def schedule_full_refresh(
        self,
        *,
        reason: str = "startup",
        trigger: TriggerType = TriggerType.MANUAL,
        scenarios: list[str] | None = None,
        n_simulations: int | None = None,
    ) -> str | None:
        detail: dict[str, Any] = {"trigger": trigger.value}
        if scenarios is not None:
            detail["scenarios_requested"] = list(scenarios)
        if n_simulations is not None:
            detail["n_simulations"] = int(n_simulations)
        with self._lock:
            if self._pipeline_busy_unlocked():
                return None
            record = self._register(JOB_FULL_REFRESH, reason=reason, detail=detail)
            self._pipeline_job_id = record.job_id
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
            if self._pipeline_busy_unlocked():
                logger.info("Skipping refit (%s): inference pipeline busy", reason)
                return None
            if self._refit_in_flight:
                logger.info("Skipping refit (%s): already in flight", reason)
                return None
            if now - self._last_refit_completed_at < self._debounce_seconds:
                logger.info("Debouncing refit (%s)", reason)
                return None
            self._refit_in_flight = True
            record = self._register(JOB_REFIT, reason=reason, detail={"trigger": trigger.value})
            self._pipeline_job_id = record.job_id
        self._executor.submit(self._safe_run, record, lambda: self._run_full_refresh(record, trigger))
        return record.job_id

    def schedule_ensemble(self, *, scenario_name: str, reason: str = "manual") -> str:
        record = self._register(JOB_ENSEMBLE, reason=reason, detail={"scenario": scenario_name})
        self._executor.submit(self._safe_run, record, lambda: self._run_single_scenario_forecast(record, scenario_name))
        return record.job_id

    def get_status(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def list_recent(self, limit: int = 20) -> list[JobRecord]:
        return sorted(self._jobs.values(), key=lambda record: record.scheduled_at, reverse=True)[:limit]

    def push_event(self, job_id: str, event: dict[str, Any]) -> None:
        record = self._jobs.get(job_id)
        if record is not None:
            record.push_event(event)

    def drain_events(self, job_id: str) -> list[dict[str, Any]]:
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
                    if self._pipeline_job_id == record.job_id:
                        self._pipeline_job_id = None

    def _run_single_scenario_forecast(self, record: JobRecord, scenario_name: str) -> None:
        cases = self._load_cases()
        if not cases:
            raise RuntimeError("No case records in the database.")
        inference = self._repository.latest_inference()
        st = get_settings()
        idx = st.hub_index_case_id
        skip_early = bool(st.hub_skip_earliest_symptom_case) and not (idx or "").strip()
        n_sims = int(record.detail.get("n_simulations") or self._ensemble_config.n_simulations)
        from eosp.services.forecast import build_forecast
        from eosp.services.hub_timeline.config import default_hub_timeline_config

        horizon_days = int(default_hub_timeline_config().horizon_days)
        sim_t0 = time.monotonic()
        timeline_total = n_sims * horizon_days

        def _progress_sink(**kw: Any) -> None:
            elapsed = time.monotonic() - sim_t0
            cdtot = int(kw.get("calendar_days_total") or horizon_days)
            di = int(kw.get("calendar_day_index") or 0)
            trj = int(kw.get("trajectory_index") or 0)
            td = min((trj - 1) * cdtot + di, timeline_total)
            record.push_event(
                {
                    "stage": "simulation",
                    "status": "active",
                    "scenario": scenario_name,
                    "scenario_index": 0,
                    "scenarios_done": 0,
                    "scenarios_total": 1,
                    "calendar_day_index": di,
                    "calendar_date": kw.get("calendar_date"),
                    "calendar_days_total": cdtot,
                    "trajectory_index": trj,
                    "trajectories_total_mc": n_sims,
                    "trajectories": td,
                    "total": timeline_total,
                    "elapsed_s": elapsed,
                    "n_simulations": n_sims,
                }
            )

        forecast = build_forecast(
            scenario_name,
            inference,
            n_simulations=n_sims,
            cases=cases,
            index_case_id=idx,
            skip_earliest_symptom_case=skip_early,
            rng_seed=self._ensemble_config.rng_seed,
            simulation_progress=_progress_sink,
        )
        if hasattr(self._repository, "cache_forecast"):
            self._repository.cache_forecast(scenario_name, forecast)
        record.detail["simulator"] = "hub_timeline"
        record.detail["scenarios_refreshed"] = [scenario_name]
        record.push_event({
            "stage": "simulation",
            "status": "complete",
            "scenarios": [scenario_name],
            "n_simulations": n_sims,
        })

    def _run_full_refresh(self, record: JobRecord, trigger: TriggerType) -> None:
        cases = self._load_cases()
        if not cases:
            raise RuntimeError(
                "No case records in the database. Ingest outbreak cases, then run analysis again."
            )

        record.push_event({
            "stage": "cases",
            "status": "complete",
            "n_cases": len(cases),
            "n_persons": int(total_cohort_persons(cases)),
            "quality_mean": validation_quality_weighted_mean(cases),
        })

        if self._inference_config.network_summary is not None:
            world_network = None
        else:
            world_network = build_default_network(cases=cases, n_days=self._ensemble_config.n_days)

        from eosp.services.inference import run_inference

        previous_inference: InferenceResult | None = None
        try:
            previous_inference = self._repository.latest_inference()
        except LookupError:
            previous_inference = None

        artifacts = run_inference(
            cases=cases,
            network=world_network,
            config=self._inference_config,
            trigger=trigger,
            previous_inference=previous_inference,
        )

        self._update_inference(artifacts.result)
        record.detail["inference_version"] = artifacts.result.version
        inference = artifacts.result
        diag = inference.diagnostics
        record.push_event({
            "stage": "inference",
            "status": "complete",
            "draw": diag.get("n_samples", self._inference_config.num_samples),
            "total": diag.get("n_samples", self._inference_config.num_samples),
            "rhat_max": diag.get(
                "rhat_max",
                max(diag["rhat"].values()) if diag.get("rhat") else 1.0,
            ),
            "divergences": diag.get("divergences", 0),
            "params": {
                k: round(float(v.mean), 4)
                for k, v in inference.parameters.items()
                if k not in ("concentration", "initial_rate")
            },
            "chain_rhat": list(diag["rhat"].values()) if diag.get("rhat") else [],
        })

        from eosp.services.forecast import build_forecast
        from eosp.services.hub_timeline.config import default_hub_timeline_config

        n_sims = int(record.detail.get("n_simulations") or self._ensemble_config.n_simulations)
        requested = record.detail.get("scenarios_requested") or list(self._scenarios.keys())
        scenarios_to_run = [scen for scen in requested if scen in self._scenarios]
        st = get_settings()
        idx = st.hub_index_case_id
        skip_early = bool(st.hub_skip_earliest_symptom_case) and not (idx or "").strip()
        refreshed: list[str] = []
        horizon_days = int(default_hub_timeline_config().horizon_days)
        timeline_total = len(scenarios_to_run) * n_sims * horizon_days

        def _sim_event(
            *,
            trajectories: int,
            scenario: str | None,
            scenarios_done: int,
            status: str,
        ) -> dict[str, Any]:
            body: dict[str, Any] = {
                "stage": "simulation",
                "status": status,
                "trajectories": trajectories,
                "total": timeline_total,
                "scenarios_done": scenarios_done,
                "scenarios_total": len(scenarios_to_run),
                "calendar_days_total": horizon_days,
                "n_simulations": n_sims,
            }
            if scenario is not None:
                body["scenario"] = scenario
            return body

        sim_t0 = time.monotonic()

        def _scenario_progress(si_plot: int, scen_label: str) -> Any:
            def sink(**kw: Any) -> None:
                elapsed = time.monotonic() - sim_t0
                cdtot = int(kw.get("calendar_days_total") or horizon_days)
                di = int(kw.get("calendar_day_index") or 0)
                trj = int(kw.get("trajectory_index") or 0)
                base = si_plot * n_sims * cdtot
                td = min(base + (trj - 1) * cdtot + di, timeline_total)
                record.push_event(
                    {
                        "stage": "simulation",
                        "status": "active",
                        "scenario": scen_label,
                        "scenario_index": si_plot,
                        "scenarios_done": si_plot,
                        "scenarios_total": len(scenarios_to_run),
                        "calendar_day_index": di,
                        "calendar_date": kw.get("calendar_date"),
                        "calendar_days_total": cdtot,
                        "trajectory_index": trj,
                        "trajectories_total_mc": n_sims,
                        "trajectories": td,
                        "total": timeline_total,
                        "elapsed_s": elapsed,
                        "n_simulations": n_sims,
                    }
                )

            return sink

        if scenarios_to_run:
            ev0 = _sim_event(
                trajectories=0,
                scenario=scenarios_to_run[0],
                scenarios_done=0,
                status="active",
            )
            ev0["elapsed_s"] = 0.0
            record.push_event(ev0)

        for si, scen in enumerate(scenarios_to_run):
            forecast = build_forecast(
                scen,
                inference,
                n_simulations=n_sims,
                cases=cases,
                index_case_id=idx,
                skip_earliest_symptom_case=skip_early,
                rng_seed=self._ensemble_config.rng_seed,
                simulation_progress=_scenario_progress(si, scen),
            )
            if hasattr(self._repository, "cache_forecast"):
                self._repository.cache_forecast(scen, forecast)
            refreshed.append(scen)
            done_units = (si + 1) * n_sims * horizon_days
            ev = _sim_event(
                trajectories=done_units,
                scenario=scen,
                scenarios_done=si + 1,
                status="active",
            )
            ev["elapsed_s"] = time.monotonic() - sim_t0
            record.push_event(ev)

        record.push_event(
            {
                "stage": "simulation",
                "status": "complete",
                "trajectories": timeline_total,
                "total": timeline_total,
                "n_simulations": n_sims,
                "scenarios": refreshed,
                "elapsed_s": (time.monotonic() - sim_t0) if scenarios_to_run else 0.0,
                "scenarios_done": len(refreshed),
                "scenarios_total": len(scenarios_to_run),
                "calendar_days_total": horizon_days,
            }
        )
        record.detail["simulator"] = "hub_timeline"
        record.detail["scenarios_refreshed"] = refreshed
        record.detail["n_simulations"] = n_sims

    def _load_cases(self) -> list[CaseRecord]:
        try:
            return list(self._repository.list_cases())
        except Exception as exc:
            logger.warning("Failed to load cases for inference: %s", exc)
            return []

    def _update_inference(self, inference: InferenceResult) -> None:
        if hasattr(self._repository, "update_inference"):
            self._repository.update_inference(inference)
        elif hasattr(self._repository, "inferences"):
            self._repository.inferences.insert(0, inference)
