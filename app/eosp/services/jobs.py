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
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Mapping

from eosp.core.case_statistics import (
    total_cohort_persons,
    total_death_equivalents,
    validation_quality_weighted_mean,
)
from eosp.core.models import CaseRecord, InferenceResult, TriggerType
from eosp.core.settings import get_settings
from eosp.services.ensemble import EnsembleConfig, run_ensemble, seed_state_from_case_counts
from eosp.services.geo import build_outbreak_geo
from eosp.services.inference import InferenceConfig, run_inference
from eosp.services.network import ContactNetwork
from eosp.services.scenarios import ScenarioSpec


logger = logging.getLogger(__name__)


JOB_REFIT = "refit"
JOB_ENSEMBLE = "ensemble"
JOB_FULL_REFRESH = "full_refresh"

_PROGRESS_EVENT_BUFFER_SIZE = 200  # bounded ring buffer; SSE drains ~every 500ms


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
        self._executor.submit(self._safe_run, record, lambda: self._run_single_ensemble(record, scenario_name))
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

        previous_inference: InferenceResult | None = None
        try:
            previous_inference = self._repository.latest_inference()
        except LookupError:
            previous_inference = None

        artifacts = run_inference(
            cases=cases,
            network=self._network,
            config=self._inference_config,
            trigger=trigger,
            previous_inference=previous_inference,
        )

        self._update_inference(artifacts.result)
        record.detail["inference_version"] = artifacts.result.version
        inference = artifacts.result
        samples = artifacts.posterior_samples
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

        n_override = record.detail.get("n_simulations")
        ensemble_cfg = (
            replace(self._ensemble_config, n_simulations=int(n_override))
            if n_override is not None
            else self._ensemble_config
        )

        requested = record.detail.get("scenarios_requested")
        if requested is None:
            scenario_items: list[tuple[str, ScenarioSpec]] = list(self._scenarios.items())
        else:
            # Dashboard and /forecasts/baseline always expect baseline cached.
            merged_request = list(dict.fromkeys(["baseline", *[n for n in requested if n != "baseline"]]))
            scenario_items = []
            for name in merged_request:
                spec = self._scenarios.get(name)
                if spec is None:
                    raise KeyError(name)
                scenario_items.append((name, spec))

        seed = self._build_seed_state(cases)
        for scenario_name, spec in scenario_items:
            total_simulations = int(ensemble_cfg.n_simulations)
            record.push_event({
                "stage": "simulation",
                "status": "running",
                "scenario": scenario_name,
                "trajectories": 0,
                "total": total_simulations,
            })

            def _callback(event: dict, _jid: str = record.job_id) -> None:
                self.push_event(_jid, event)

            response = run_ensemble(
                scenario=spec,
                inference=inference,
                network=self._network,
                seed=seed,
                config=ensemble_cfg,
                posterior_samples=samples,
                progress_callback=_callback,
            )
            self._cache_forecast(scenario_name, response)
            record.push_event({
                "stage": "simulation",
                "status": "complete",
                "scenario": scenario_name,
                "trajectories": total_simulations,
                "total": total_simulations,
                "elapsed_s": response.metadata.get("execution_time_seconds"),
            })
        record.detail["scenarios_refreshed"] = [n for n, _ in scenario_items]

        _st = get_settings()
        if (
            _st.metapop_enabled
            and (_st.geo_risk_model or "").strip().lower() == "metapop"
        ):
            n_runs_env = int(os.environ.get("EOSP_METAPOP_RUNS", "64"))
            record.push_event({
                "stage": "metapop_geo",
                "status": "running",
                "metapop_n_runs": n_runs_env,
            })
            geo_detail: dict[str, Any]
            try:
                p_transmit = (
                    float(inference.parameters["p_transmit"].mean)
                    if "p_transmit" in inference.parameters
                    else 1.5 / 21.5
                )
                geo_payload = build_outbreak_geo(
                    p_transmit=p_transmit,
                    cases=cases,
                    risk_model="metapop",
                    metapop_n_runs=n_runs_env,
                    metapop_progress_callback=lambda ev: self.push_event(
                        record.job_id,
                        {
                            "stage": "metapop_geo",
                            "status": "running",
                            "runs_completed": ev["runs_completed"],
                            "total_runs": ev["total_runs"],
                            "metapop_n_runs": n_runs_env,
                            "elapsed_s": ev.get("elapsed_s"),
                        },
                    ),
                )
                if hasattr(self._repository, "cache_geo_outbreak"):
                    self._repository.cache_geo_outbreak(inference.version, geo_payload)
                meta = geo_payload.get("metadata") or {}
                geo_detail = {
                    "status": "complete",
                    "metapop_n_runs": meta.get("metapop_n_runs"),
                    "p_transmit_used": meta.get("p_transmit_used"),
                    "risk_source": meta.get("risk_source"),
                }
                record.push_event(
                    {
                        "stage": "metapop_geo",
                        "status": "complete",
                        **geo_detail,
                    }
                )
            except Exception as exc:  # noqa: BLE001 — keep job successful; map can rebuild on GET
                logger.exception("Metapop geo stage failed after ensemble: %s", exc)
                geo_detail = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                record.push_event({"stage": "metapop_geo", **geo_detail})
            record.detail["metapop_geo"] = geo_detail

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
        n_total = int(total_cohort_persons(cases))
        n_deceased = int(total_death_equivalents(cases))
        n_observed_alive = max(0, n_total - n_deceased)
        n_recovered = max(0, n_observed_alive - 2)
        n_active = min(2, n_observed_alive)
        hidden_multiplier = 1.5
        n_hidden_exposed = max(4, int(round(n_total * hidden_multiplier)))
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
