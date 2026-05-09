from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from uuid import uuid4

from sqlalchemy import func

from eosp.core.case_statistics import case_summary_person_totals
from eosp.core.models import (
    CaseCreate,
    CaseRecord,
    CaseStatus,
    CaseUpdate,
    ForecastResponse,
    ForecastRunItem,
    ForecastRunResponse,
    InferenceResult,
    LabResult,
    ObservationKind,
    QualityAlert,
    ValidationResult,
    assert_case_cohort_consistency,
)
from eosp.core.tables import (
    CaseRecordHistoryRow,
    CaseRecordRow,
    ExternalFeedStateRow,
    ForecastResultRow,
    InferenceTraceRow,
    QualityAlertRow,
    UserActionRow,
    ValidationResultRow,
)
from eosp.services.validation import validate_case


@dataclass(slots=True)
class _StoredFeed:
    fingerprint: str
    last_checked_at: datetime
    last_changed_at: datetime | None


@dataclass
class InMemoryRepository:
    cases: list[CaseRecord]
    validations: dict[UUID, ValidationResult]
    inferences: list[InferenceResult]
    alerts: list[QualityAlert]
    forecast_runs: list[ForecastRunItem] | None = None
    forecast_cache: dict[str, ForecastResponse] = field(default_factory=dict)
    external_feed_snapshots: dict[str, _StoredFeed] = field(default_factory=dict)
    geo_outbreak_cache: dict[str, Any] | None = None
    geo_outbreak_cache_inference_version: str | None = None

    def case_summary(self):
        feed_last = max(
            (f.last_checked_at for f in self.external_feed_snapshots.values()),
            default=None,
        )
        if not self.cases:
            return 0, 0, 0, datetime.now(UTC), {}, feed_last
        confirmed, suspected, deaths, sources = case_summary_person_totals(self.cases)
        last_updated = max(c.record_updated_at for c in self.cases)
        return confirmed, suspected, deaths, last_updated, sources, feed_last

    def list_cases(self, country: str | None = None, status: CaseStatus | None = None) -> list[CaseRecord]:
        records = self.cases
        if country:
            records = [case for case in records if case.location_country.upper() == country.upper()]
        if status:
            records = [case for case in records if case.confirmed_or_suspected == status]
        return sorted(records, key=lambda case: case.symptom_onset_date)

    def load_dashboard_cases_bundle(
        self,
    ) -> tuple[list[CaseRecord], tuple[int, int, int, datetime, dict[str, int], datetime | None]]:
        """Single pass over in-memory case rows plus feed timestamp for dashboard bootstrap."""

        feed_last = max(
            (f.last_checked_at for f in self.external_feed_snapshots.values()),
            default=None,
        )
        cases = sorted(self.cases, key=lambda case: case.symptom_onset_date)
        if not cases:
            return [], (0, 0, 0, datetime.now(UTC), {}, feed_last)
        confirmed, suspected, deaths, sources = case_summary_person_totals(cases)
        last_updated = max(c.record_updated_at for c in cases)
        return cases, (confirmed, suspected, deaths, last_updated, sources, feed_last)

    def get_validation(self, case_id: UUID) -> ValidationResult | None:
        return self.validations.get(case_id)

    def create_case(self, payload: CaseCreate) -> tuple[CaseRecord, ValidationResult]:
        now = datetime.now(UTC)
        ts = payload.ingestion_timestamp or now
        case = CaseRecord(
            case_id=payload.case_id,
            patient_identifier=payload.patient_identifier,
            symptom_onset_date=payload.symptom_onset_date,
            hospitalization_date=payload.hospitalization_date,
            death_date=payload.death_date,
            location_country=payload.location_country.upper(),
            location_airport_code=payload.location_airport_code.upper() if payload.location_airport_code else None,
            confirmed_or_suspected=payload.confirmed_or_suspected,
            lab_test_result=payload.lab_test_result,
            contacts=payload.contacts,
            data_source=payload.data_source,
            ingestion_timestamp=ts,
            record_updated_at=ts,
            validation_score=0,
            updated_by=payload.updated_by,
            updated_reason=payload.updated_reason,
            observation_kind=payload.observation_kind,
            cohort_size=payload.cohort_size,
            cohort_deaths=payload.cohort_deaths,
            report_period_start=payload.report_period_start,
            report_period_end=payload.report_period_end,
            external_observation_key=payload.external_observation_key,
        )
        validation = validate_case(case, [*self.cases, case])
        case.validation_score = validation.quality_score
        self.cases.append(case)
        self.validations[case.case_id] = validation
        if validation.quality_score < 0.75:
            self.alerts.append(_alert_for_validation(case, validation))
        return case, validation

    def get_case(self, case_id: UUID) -> CaseRecord | None:
        for case in self.cases:
            if case.case_id == case_id:
                return case
        return None

    def update_case(self, case_id: UUID, patch: CaseUpdate) -> tuple[CaseRecord, ValidationResult]:
        if not patch.model_dump(exclude_unset=True):
            raise ValueError("no fields to update")
        idx = next((i for i, c in enumerate(self.cases) if c.case_id == case_id), None)
        if idx is None:
            raise LookupError(str(case_id))
        old = self.cases[idx]
        merged = _merge_case_record(old, patch).model_copy(
            update={
                "version": old.version + 1,
                "record_updated_at": datetime.now(UTC),
            }
        )
        assert_case_cohort_consistency(merged)
        peers = [c for c in self.cases if c.case_id != case_id]
        validation = validate_case(merged, [*peers, merged])
        merged = merged.model_copy(update={"validation_score": validation.quality_score})
        self.cases[idx] = merged
        self.validations[case_id] = validation
        if validation.quality_score < 0.75:
            self.alerts.append(_alert_for_validation(merged, validation))
        return merged, validation

    def delete_case(self, case_id: UUID) -> bool:
        for i, c in enumerate(self.cases):
            if c.case_id == case_id:
                self.cases.pop(i)
                self.validations.pop(case_id, None)
                self.alerts[:] = [a for a in self.alerts if a.case_id != case_id]
                return True
        return False

    def upsert_external_observation(self, payload: CaseCreate) -> tuple[CaseRecord, ValidationResult, bool]:
        if not payload.external_observation_key:
            raise ValueError("upsert_external_observation requires external_observation_key")
        key_fields = (
            payload.cohort_size,
            payload.cohort_deaths,
            payload.symptom_onset_date,
            payload.location_country.upper(),
            payload.confirmed_or_suspected,
            payload.lab_test_result,
        )
        idx = next(
            (i for i, c in enumerate(self.cases) if c.external_observation_key == payload.external_observation_key),
            None,
        )
        if idx is not None:
            old = self.cases[idx]
            old_sig = (
                old.cohort_size,
                old.cohort_deaths,
                old.symptom_onset_date,
                old.location_country,
                old.confirmed_or_suspected,
                old.lab_test_result,
            )
            if old_sig == key_fields:
                val = self.validations.get(old.case_id)
                assert val is not None
                return old, val, False
            kept_id = old.case_id
        else:
            kept_id = payload.case_id

        now = datetime.now(UTC)
        ts = payload.ingestion_timestamp or now
        case = CaseRecord(
            case_id=kept_id,
            patient_identifier=payload.patient_identifier,
            symptom_onset_date=payload.symptom_onset_date,
            hospitalization_date=payload.hospitalization_date,
            death_date=payload.death_date,
            location_country=payload.location_country.upper(),
            location_airport_code=payload.location_airport_code.upper() if payload.location_airport_code else None,
            confirmed_or_suspected=payload.confirmed_or_suspected,
            lab_test_result=payload.lab_test_result,
            contacts=payload.contacts,
            data_source=payload.data_source,
            ingestion_timestamp=ts,
            record_updated_at=now,
            validation_score=0,
            updated_by=payload.updated_by,
            updated_reason=payload.updated_reason,
            observation_kind=payload.observation_kind,
            cohort_size=payload.cohort_size,
            cohort_deaths=payload.cohort_deaths,
            report_period_start=payload.report_period_start,
            report_period_end=payload.report_period_end,
            external_observation_key=payload.external_observation_key,
            version=(self.cases[idx].version + 1) if idx is not None else 1,
        )
        peer_pool = [c for c in self.cases if c.external_observation_key != payload.external_observation_key]
        validation = validate_case(case, [*peer_pool, case])
        case.validation_score = validation.quality_score

        if idx is not None:
            self.cases[idx] = case
        else:
            self.cases.append(case)
        self.validations[case.case_id] = validation
        if validation.quality_score < 0.75:
            self.alerts.append(_alert_for_validation(case, validation))
        return case, validation, True

    def latest_inference(self) -> InferenceResult:
        if not self.inferences:
            raise LookupError("No inference traces available")
        return self.inferences[0]

    def get_inference(self, version: str) -> InferenceResult | None:
        if version == "latest":
            try:
                return self.latest_inference()
            except LookupError:
                return None
        for inference in self.inferences:
            if inference.version == version:
                return inference
        return None

    def save_forecast_run(self, item: ForecastRunItem, forecast_json: dict) -> None:
        if self.forecast_runs is None:
            self.forecast_runs = []
        self.forecast_runs.insert(0, item)

    def list_forecast_runs(self, limit: int = 10) -> list[ForecastRunItem]:
        return (self.forecast_runs or [])[:limit]

    def cache_forecast(self, scenario: str, response: ForecastResponse) -> None:
        self.forecast_cache[scenario] = response

    def get_cached_forecast(self, scenario: str, model_version: str | None = None) -> ForecastResponse | None:
        cached = self.forecast_cache.get(scenario)
        if cached is None:
            return None
        if model_version and model_version != "latest" and cached.metadata.get("model_version") != model_version:
            return None
        return cached

    def cache_geo_outbreak(self, inference_version: str, payload: dict[str, Any]) -> None:
        self.geo_outbreak_cache_inference_version = inference_version
        self.geo_outbreak_cache = payload

    def get_geo_outbreak_cache(self, inference_version: str) -> dict[str, Any] | None:
        if self.geo_outbreak_cache is None or self.geo_outbreak_cache_inference_version != inference_version:
            return None
        return self.geo_outbreak_cache

    def update_inference(self, inference: InferenceResult) -> None:
        self.inferences.insert(0, inference)

    def record_external_feed_poll(
        self, feed_key: str, fingerprint: str, *, treat_first_poll_as_changed: bool = False
    ) -> bool:
        now = datetime.now(UTC)
        prev = self.external_feed_snapshots.get(feed_key)
        if prev is None:
            self.external_feed_snapshots[feed_key] = _StoredFeed(
                fingerprint=fingerprint,
                last_checked_at=now,
                last_changed_at=None,
            )
            return bool(treat_first_poll_as_changed)
        changed = prev.fingerprint != fingerprint
        self.external_feed_snapshots[feed_key] = _StoredFeed(
            fingerprint=fingerprint,
            last_checked_at=now,
            last_changed_at=now if changed else prev.last_changed_at,
        )
        return changed


def empty_repository() -> InMemoryRepository:
    return InMemoryRepository(cases=[], validations={}, inferences=[], alerts=[])


@dataclass
class SqlRepository:
    session_factory: object
    geo_outbreak_cache: dict[str, Any] | None = field(default=None, repr=False)
    geo_outbreak_cache_inference_version: str | None = field(default=None, repr=False)

    def case_summary(self):
        with self.session_factory() as session:
            feed_last = session.query(func.max(ExternalFeedStateRow.last_checked_at)).scalar()
            records = session.query(CaseRecordRow).filter(CaseRecordRow.active.is_(True)).all()
            if not records:
                return 0, 0, 0, datetime.now(UTC), {}, feed_last
            models = [_case_from_row(row) for row in records]
            confirmed, suspected, deaths, sources = case_summary_person_totals(models)
            last_updated = max(_sql_row_latest_activity(row) for row in records)
            return confirmed, suspected, deaths, last_updated, sources, feed_last

    def list_cases(self, country: str | None = None, status: CaseStatus | None = None) -> list[CaseRecord]:
        with self.session_factory() as session:
            query = session.query(CaseRecordRow).filter(CaseRecordRow.active.is_(True))
            if country:
                query = query.filter(CaseRecordRow.location_country == country.upper())
            if status:
                query = query.filter(CaseRecordRow.confirmed_or_suspected == status.value)
            rows = query.order_by(CaseRecordRow.symptom_onset_date).all()
            return [_case_from_row(row) for row in rows]

    def load_dashboard_cases_bundle(
        self,
    ) -> tuple[list[CaseRecord], tuple[int, int, int, datetime, dict[str, int], datetime | None]]:
        """One query for active cases, one scalar for feed heartbeat — avoids duplicate full-table reads."""

        with self.session_factory() as session:
            feed_last = session.query(func.max(ExternalFeedStateRow.last_checked_at)).scalar()
            rows = (
                session.query(CaseRecordRow)
                .filter(CaseRecordRow.active.is_(True))
                .order_by(CaseRecordRow.symptom_onset_date)
                .all()
            )
            if not rows:
                return [], (0, 0, 0, datetime.now(UTC), {}, feed_last)
            models = [_case_from_row(row) for row in rows]
            confirmed, suspected, deaths, sources = case_summary_person_totals(models)
            last_updated = max(_sql_row_latest_activity(row) for row in rows)
            return models, (confirmed, suspected, deaths, last_updated, sources, feed_last)

    def get_validation(self, case_id: UUID) -> ValidationResult | None:
        with self.session_factory() as session:
            row = (
                session.query(ValidationResultRow)
                .filter(ValidationResultRow.case_id == case_id)
                .order_by(ValidationResultRow.validation_timestamp.desc())
                .first()
            )
            if row is None:
                return None
            return ValidationResult(
                case_id=row.case_id,
                checks_passed=row.checks_passed,
                issues=row.issues,
                quality_score=row.quality_score,
                quarantine_status=row.quarantine_status,
            )

    def create_case(self, payload: CaseCreate) -> tuple[CaseRecord, ValidationResult]:
        with self.session_factory() as session:
            existing = session.get(CaseRecordRow, payload.case_id)
            if existing is not None:
                raise ValueError(f"case_id already exists: {payload.case_id}")

            existing_cases = [_case_from_row(row) for row in session.query(CaseRecordRow).filter(CaseRecordRow.active.is_(True)).all()]
            now = datetime.now(UTC)
            ts = payload.ingestion_timestamp or now
            case = CaseRecord(
                case_id=payload.case_id,
                patient_identifier=payload.patient_identifier,
                symptom_onset_date=payload.symptom_onset_date,
                hospitalization_date=payload.hospitalization_date,
                death_date=payload.death_date,
                location_country=payload.location_country.upper(),
                location_airport_code=payload.location_airport_code.upper() if payload.location_airport_code else None,
                confirmed_or_suspected=payload.confirmed_or_suspected,
                lab_test_result=payload.lab_test_result,
                contacts=payload.contacts,
                data_source=payload.data_source,
                ingestion_timestamp=ts,
                record_updated_at=ts,
                validation_score=0,
                updated_by=payload.updated_by,
                updated_reason=payload.updated_reason,
                observation_kind=payload.observation_kind,
                cohort_size=payload.cohort_size,
                cohort_deaths=payload.cohort_deaths,
                report_period_start=payload.report_period_start,
                report_period_end=payload.report_period_end,
                external_observation_key=payload.external_observation_key,
            )
            validation = validate_case(case, [*existing_cases, case])
            case.validation_score = validation.quality_score

            session.add(_case_to_row(case))
            session.flush()
            session.add(
                CaseRecordHistoryRow(
                    case_id=case.case_id,
                    case_data_json=case.model_dump(mode="json"),
                    source=case.data_source,
                    validation_score=case.validation_score,
                    change_reason=case.updated_reason,
                    changed_by=case.updated_by,
                    parent_version_id=case.version,
                )
            )
            session.add(
                ValidationResultRow(
                    validation_id=uuid4(),
                    case_id=case.case_id,
                    checks_passed=validation.checks_passed,
                    issues=validation.issues,
                    quality_score=validation.quality_score,
                    quarantine_status=validation.quarantine_status,
                )
            )
            if validation.quality_score < 0.75:
                session.add(_alert_to_row(_alert_for_validation(case, validation)))
            session.add(
                UserActionRow(
                    user_id=case.updated_by,
                    action_type="create_case",
                    resource_type="case",
                    resource_id=str(case.case_id),
                    details={"validation_score": case.validation_score, "quarantine_status": validation.quarantine_status},
                    ip_address=None,
                )
            )
            session.commit()
            return case, validation

    def get_case(self, case_id: UUID) -> CaseRecord | None:
        with self.session_factory() as session:
            row = session.get(CaseRecordRow, case_id)
            if row is None or not row.active:
                return None
            return _case_from_row(row)

    def update_case(self, case_id: UUID, patch: CaseUpdate) -> tuple[CaseRecord, ValidationResult]:
        if not patch.model_dump(exclude_unset=True):
            raise ValueError("no fields to update")
        with self.session_factory() as session:
            row = session.get(CaseRecordRow, case_id)
            if row is None or not row.active:
                raise LookupError(str(case_id))
            old = _case_from_row(row)
            merged = _merge_case_record(old, patch)
            merged.version = old.version + 1
            assert_case_cohort_consistency(merged)
            peer_rows = [
                r
                for r in session.query(CaseRecordRow).filter(CaseRecordRow.active.is_(True)).all()
                if r.case_id != case_id
            ]
            peer_models = [_case_from_row(r) for r in peer_rows]
            validation = validate_case(merged, [*peer_models, merged])
            merged.validation_score = validation.quality_score
            touch = datetime.now(UTC)
            merged = merged.model_copy(update={"record_updated_at": touch})

            row.patient_identifier = merged.patient_identifier
            row.symptom_onset_date = merged.symptom_onset_date
            row.hospitalization_date = merged.hospitalization_date
            row.death_date = merged.death_date
            row.location_country = merged.location_country
            row.location_airport_code = merged.location_airport_code
            row.confirmed_or_suspected = merged.confirmed_or_suspected.value
            row.lab_test_result = merged.lab_test_result.value
            row.contacts = merged.contacts
            row.data_source = merged.data_source
            row.validation_score = merged.validation_score
            row.version = merged.version
            row.updated_by = merged.updated_by
            row.updated_reason = merged.updated_reason
            row.observation_kind = merged.observation_kind.value
            row.cohort_size = merged.cohort_size
            row.cohort_deaths = merged.cohort_deaths
            row.report_period_start = merged.report_period_start
            row.report_period_end = merged.report_period_end
            row.updated_at = touch
            session.flush()
            session.add(
                CaseRecordHistoryRow(
                    case_id=merged.case_id,
                    case_data_json=merged.model_dump(mode="json"),
                    source=merged.data_source,
                    validation_score=merged.validation_score,
                    change_reason=merged.updated_reason,
                    changed_by=merged.updated_by,
                    parent_version_id=merged.version,
                )
            )
            session.add(
                ValidationResultRow(
                    validation_id=uuid4(),
                    case_id=merged.case_id,
                    checks_passed=validation.checks_passed,
                    issues=validation.issues,
                    quality_score=validation.quality_score,
                    quarantine_status=validation.quarantine_status,
                )
            )
            if validation.quality_score < 0.75:
                session.add(_alert_to_row(_alert_for_validation(merged, validation)))
            session.add(
                UserActionRow(
                    user_id=merged.updated_by,
                    action_type="update_case",
                    resource_type="case",
                    resource_id=str(merged.case_id),
                    details={"validation_score": merged.validation_score, "quarantine_status": validation.quarantine_status},
                    ip_address=None,
                )
            )
            session.commit()
            return merged, validation

    def delete_case(self, case_id: UUID) -> bool:
        with self.session_factory() as session:
            row = session.get(CaseRecordRow, case_id)
            if row is None or not row.active:
                return False
            row.active = False
            row.updated_at = datetime.now(UTC)
            session.add(
                UserActionRow(
                    user_id="researcher_console",
                    action_type="delete_case",
                    resource_type="case",
                    resource_id=str(case_id),
                    details={},
                    ip_address=None,
                )
            )
            session.commit()
            return True

    def upsert_external_observation(self, payload: CaseCreate) -> tuple[CaseRecord, ValidationResult, bool]:
        if not payload.external_observation_key:
            raise ValueError("upsert_external_observation requires external_observation_key")

        def _validation_from_session(sess, case_id: UUID) -> ValidationResult:
            vrow = (
                sess.query(ValidationResultRow)
                .filter(ValidationResultRow.case_id == case_id)
                .order_by(ValidationResultRow.validation_timestamp.desc())
                .first()
            )
            assert vrow is not None
            return ValidationResult(
                case_id=vrow.case_id,
                checks_passed=vrow.checks_passed,
                issues=vrow.issues,
                quality_score=vrow.quality_score,
                quarantine_status=vrow.quarantine_status,
            )

        with self.session_factory() as session:
            row = (
                session.query(CaseRecordRow)
                .filter(
                    CaseRecordRow.external_observation_key == payload.external_observation_key,
                    CaseRecordRow.active.is_(True),
                )
                .first()
            )
            peer_rows = [
                r
                for r in session.query(CaseRecordRow).filter(CaseRecordRow.active.is_(True)).all()
                if r.external_observation_key != payload.external_observation_key
            ]
            peer_models = [_case_from_row(r) for r in peer_rows]

            old_case: CaseRecord | None = None
            prev_version = 0
            if row is not None:
                old_case = _case_from_row(row)
                sig_new = (
                    payload.cohort_size,
                    payload.cohort_deaths,
                    payload.symptom_onset_date,
                    payload.location_country.upper(),
                    payload.confirmed_or_suspected,
                    payload.lab_test_result,
                )
                sig_old = (
                    old_case.cohort_size,
                    old_case.cohort_deaths,
                    old_case.symptom_onset_date,
                    old_case.location_country,
                    old_case.confirmed_or_suspected,
                    old_case.lab_test_result,
                )
                if sig_old == sig_new:
                    return old_case, _validation_from_session(session, old_case.case_id), False
                prev_version = old_case.version

            now = datetime.now(UTC)
            cid = row.case_id if row is not None else payload.case_id
            if row is None:
                collision = session.get(CaseRecordRow, cid)
                if collision is not None:
                    raise ValueError(f"case_id already exists: {cid}")

            case = CaseRecord(
                case_id=cid,
                patient_identifier=payload.patient_identifier,
                symptom_onset_date=payload.symptom_onset_date,
                hospitalization_date=payload.hospitalization_date,
                death_date=payload.death_date,
                location_country=payload.location_country.upper(),
                location_airport_code=payload.location_airport_code.upper() if payload.location_airport_code else None,
                confirmed_or_suspected=payload.confirmed_or_suspected,
                lab_test_result=payload.lab_test_result,
                contacts=payload.contacts,
                data_source=payload.data_source,
                ingestion_timestamp=payload.ingestion_timestamp or now,
                validation_score=0,
                updated_by=payload.updated_by,
                updated_reason=payload.updated_reason,
                observation_kind=payload.observation_kind,
                cohort_size=payload.cohort_size,
                cohort_deaths=payload.cohort_deaths,
                report_period_start=payload.report_period_start,
                report_period_end=payload.report_period_end,
                external_observation_key=payload.external_observation_key,
                version=prev_version + 1,
                record_updated_at=now,
            )
            validation = validate_case(case, [*peer_models, case])
            case.validation_score = validation.quality_score

            if row is None:
                session.add(_case_to_row(case))
            else:
                row.patient_identifier = case.patient_identifier
                row.symptom_onset_date = case.symptom_onset_date
                row.hospitalization_date = case.hospitalization_date
                row.death_date = case.death_date
                row.location_country = case.location_country
                row.location_airport_code = case.location_airport_code
                row.confirmed_or_suspected = case.confirmed_or_suspected.value
                row.lab_test_result = case.lab_test_result.value
                row.contacts = case.contacts
                row.data_source = case.data_source
                row.ingestion_timestamp = case.ingestion_timestamp
                row.validation_score = case.validation_score
                row.version = case.version
                row.updated_by = case.updated_by
                row.updated_reason = case.updated_reason
                row.observation_kind = case.observation_kind.value
                row.cohort_size = case.cohort_size
                row.cohort_deaths = case.cohort_deaths
                row.report_period_start = case.report_period_start
                row.report_period_end = case.report_period_end
                row.external_observation_key = case.external_observation_key
                row.updated_at = now
            session.flush()
            session.add(
                CaseRecordHistoryRow(
                    case_id=case.case_id,
                    case_data_json=case.model_dump(mode="json"),
                    source=case.data_source,
                    validation_score=case.validation_score,
                    change_reason=case.updated_reason,
                    changed_by=case.updated_by,
                    parent_version_id=case.version,
                )
            )
            session.add(
                ValidationResultRow(
                    validation_id=uuid4(),
                    case_id=case.case_id,
                    checks_passed=validation.checks_passed,
                    issues=validation.issues,
                    quality_score=validation.quality_score,
                    quarantine_status=validation.quarantine_status,
                )
            )
            if validation.quality_score < 0.75:
                session.add(_alert_to_row(_alert_for_validation(case, validation)))
            session.add(
                UserActionRow(
                    user_id=case.updated_by,
                    action_type="upsert_external_observation",
                    resource_type="case",
                    resource_id=str(case.case_id),
                    details={
                        "external_observation_key": payload.external_observation_key,
                        "validation_score": case.validation_score,
                    },
                    ip_address=None,
                )
            )
            session.commit()
            return case, validation, True

    def latest_inference(self) -> InferenceResult:
        with self.session_factory() as session:
            row = session.query(InferenceTraceRow).order_by(InferenceTraceRow.timestamp.desc()).first()
            if row is None:
                raise LookupError("No inference traces have been seeded")
            return _inference_from_row(row)

    def get_inference(self, version: str) -> InferenceResult | None:
        with self.session_factory() as session:
            if version == "latest":
                row = session.query(InferenceTraceRow).order_by(InferenceTraceRow.timestamp.desc()).first()
            else:
                row = session.query(InferenceTraceRow).filter(InferenceTraceRow.version == version).first()
            return _inference_from_row(row) if row else None

    def save_forecast_run(self, item: ForecastRunItem, forecast_json: dict) -> None:
        with self.session_factory() as session:
            session.add(
                ForecastResultRow(
                    forecast_id=item.forecast_id,
                    model_version=item.model_version,
                    scenario=item.scenario,
                    forecast_json=forecast_json,
                    n_simulations=item.n_simulations,
                    execution_time_seconds=item.execution_time_seconds,
                    s3_archive_path=None,
                )
            )
            session.add(
                UserActionRow(
                    user_id="forecast_engine",
                    action_type="run_forecast",
                    resource_type="forecast",
                    resource_id=str(item.forecast_id),
                    details={
                        "scenario": item.scenario,
                        "model_version": item.model_version,
                        "n_simulations": item.n_simulations,
                    },
                    ip_address=None,
                )
            )
            session.commit()

    def list_forecast_runs(self, limit: int = 10) -> list[ForecastRunItem]:
        with self.session_factory() as session:
            rows = (
                session.query(ForecastResultRow)
                .order_by(ForecastResultRow.generated_timestamp.desc())
                .limit(limit)
                .all()
            )
            items: list[ForecastRunItem] = []
            for row in rows:
                forecast = row.forecast_json["forecast"]
                final_point = forecast[-1]
                items.append(
                    ForecastRunItem(
                        forecast_id=row.forecast_id,
                        scenario=row.scenario,
                        model_version=row.model_version,
                        n_simulations=row.n_simulations,
                        execution_time_seconds=row.execution_time_seconds,
                        cases_day_14=final_point["cases_cumulative"],
                        deaths_day_14=final_point["deaths_cumulative"],
                    )
                )
            return items

    def cache_forecast(self, scenario: str, response: ForecastResponse) -> None:
        model_version = str(response.metadata.get("model_version", "latest"))
        with self.session_factory() as session:
            payload = response.model_dump(mode="json")
            session.add(
                ForecastResultRow(
                    forecast_id=uuid4(),
                    model_version=model_version,
                    scenario=scenario,
                    forecast_json=payload,
                    n_simulations=int(response.metadata.get("n_simulations", 10000)),
                    execution_time_seconds=float(response.metadata.get("execution_time_seconds", 0.0)),
                    s3_archive_path=None,
                )
            )
            session.commit()

    def get_cached_forecast(self, scenario: str, model_version: str | None = None) -> ForecastResponse | None:
        with self.session_factory() as session:
            query = session.query(ForecastResultRow).filter(ForecastResultRow.scenario == scenario)
            if model_version and model_version != "latest":
                query = query.filter(ForecastResultRow.model_version == model_version)
            row = query.order_by(ForecastResultRow.generated_timestamp.desc()).first()
            if row is None:
                return None
            return ForecastResponse.model_validate(row.forecast_json)

    def cache_geo_outbreak(self, inference_version: str, payload: dict[str, Any]) -> None:
        self.geo_outbreak_cache_inference_version = inference_version
        self.geo_outbreak_cache = payload

    def get_geo_outbreak_cache(self, inference_version: str) -> dict[str, Any] | None:
        if self.geo_outbreak_cache is None or self.geo_outbreak_cache_inference_version != inference_version:
            return None
        return self.geo_outbreak_cache

    def update_inference(self, inference: InferenceResult) -> None:
        with self.session_factory() as session:
            row = session.query(InferenceTraceRow).filter(InferenceTraceRow.version == inference.version).first()
            payload = {
                name: estimate.model_dump(mode="json")
                for name, estimate in inference.parameters.items()
            }
            if row is None:
                session.add(
                    InferenceTraceRow(
                        trace_id=uuid4(),
                        version=inference.version,
                        timestamp=inference.timestamp,
                        trigger_type=inference.trigger.value,
                        n_cases=inference.n_cases,
                        posterior_s3_path=inference.posterior_download_url,
                        posterior_summary=payload,
                        diagnostics=inference.diagnostics,
                        execution_time_seconds=float(inference.diagnostics.get("execution_time_seconds", 0.0)),
                        data_quality_mean=float(inference.diagnostics.get("data_quality_mean", 0.84)),
                        convergence_status=inference.diagnostics.get("convergence_status", "CONVERGED"),
                    )
                )
            else:
                row.timestamp = inference.timestamp
                row.trigger_type = inference.trigger.value
                row.n_cases = inference.n_cases
                row.posterior_s3_path = inference.posterior_download_url
                row.posterior_summary = payload
                row.diagnostics = inference.diagnostics
                row.convergence_status = inference.diagnostics.get("convergence_status", row.convergence_status)
            session.commit()

    @property
    def inferences(self) -> list[InferenceResult]:
        with self.session_factory() as session:
            rows = session.query(InferenceTraceRow).order_by(InferenceTraceRow.timestamp.desc()).all()
            return [_inference_from_row(row) for row in rows]

    @property
    def alerts(self) -> list[QualityAlert]:
        with self.session_factory() as session:
            rows = session.query(QualityAlertRow).order_by(QualityAlertRow.timestamp.desc()).all()
            return [
                QualityAlert(
                    alert_id=row.alert_id,
                    severity=row.severity,
                    case_id=row.case_id,
                    issue=row.issue,
                    timestamp=row.timestamp,
                    recommended_action=row.recommended_action,
                )
                for row in rows
            ]

    def record_external_feed_poll(
        self, feed_key: str, fingerprint: str, *, treat_first_poll_as_changed: bool = False
    ) -> bool:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            row = session.get(ExternalFeedStateRow, feed_key)
            if row is None:
                session.add(
                    ExternalFeedStateRow(
                        feed_key=feed_key,
                        content_fingerprint=fingerprint,
                        last_checked_at=now,
                        last_changed_at=None,
                    )
                )
                session.commit()
                return bool(treat_first_poll_as_changed)
            changed = row.content_fingerprint != fingerprint
            row.content_fingerprint = fingerprint
            row.last_checked_at = now
            if changed:
                row.last_changed_at = now
            session.commit()
            return changed


def _sql_row_latest_activity(row: CaseRecordRow) -> datetime:
    ing = row.ingestion_timestamp
    up = getattr(row, "updated_at", None)
    return max(ing, up) if up is not None else ing


def _merge_case_record(base: CaseRecord, patch: CaseUpdate) -> CaseRecord:
    raw = patch.model_dump(exclude_unset=True)
    if "location_country" in raw and raw["location_country"]:
        raw["location_country"] = str(raw["location_country"]).upper()
    if "location_airport_code" in raw:
        la = raw["location_airport_code"]
        raw["location_airport_code"] = la.upper() if la else None
    return base.model_copy(update=raw)


def _case_from_row(row: CaseRecordRow) -> CaseRecord:
    kind_raw = getattr(row, "observation_kind", None) or "individual"
    return CaseRecord(
        case_id=row.case_id,
        patient_identifier=row.patient_identifier,
        symptom_onset_date=row.symptom_onset_date,
        hospitalization_date=row.hospitalization_date,
        death_date=row.death_date,
        location_country=row.location_country,
        location_airport_code=row.location_airport_code,
        confirmed_or_suspected=CaseStatus(row.confirmed_or_suspected),
        lab_test_result=LabResult(row.lab_test_result),
        contacts=row.contacts,
        data_source=row.data_source,
        ingestion_timestamp=row.ingestion_timestamp,
        validation_score=row.validation_score,
        version=row.version,
        updated_by=row.updated_by,
        updated_reason=row.updated_reason,
        observation_kind=ObservationKind(kind_raw),
        cohort_size=int(getattr(row, "cohort_size", 1) or 1),
        cohort_deaths=int(getattr(row, "cohort_deaths", 0) or 0),
        report_period_start=getattr(row, "report_period_start", None),
        report_period_end=getattr(row, "report_period_end", None),
        external_observation_key=getattr(row, "external_observation_key", None),
        record_updated_at=getattr(row, "updated_at", row.ingestion_timestamp),
    )


def _case_to_row(case: CaseRecord) -> CaseRecordRow:
    touch = case.record_updated_at or case.ingestion_timestamp
    return CaseRecordRow(
        case_id=case.case_id,
        patient_identifier=case.patient_identifier,
        symptom_onset_date=case.symptom_onset_date,
        hospitalization_date=case.hospitalization_date,
        death_date=case.death_date,
        location_country=case.location_country,
        location_airport_code=case.location_airport_code,
        confirmed_or_suspected=case.confirmed_or_suspected.value,
        lab_test_result=case.lab_test_result.value,
        contacts=case.contacts,
        data_source=case.data_source,
        ingestion_timestamp=case.ingestion_timestamp,
        validation_score=case.validation_score,
        version=case.version,
        updated_by=case.updated_by,
        updated_reason=case.updated_reason,
        observation_kind=case.observation_kind.value,
        cohort_size=case.cohort_size,
        cohort_deaths=case.cohort_deaths,
        report_period_start=case.report_period_start,
        report_period_end=case.report_period_end,
        external_observation_key=case.external_observation_key,
        updated_at=touch,
    )


def _alert_for_validation(case: CaseRecord, validation: ValidationResult) -> QualityAlert:
    issue = validation.issues[0] if validation.issues else "Validation score below admin review threshold"
    return QualityAlert(
        alert_id=f"alert_{case.case_id.hex[:12]}",
        severity="HIGH" if validation.quality_score < 0.65 else "MEDIUM",
        case_id=case.case_id,
        issue=issue,
        timestamp=datetime.now(UTC),
        recommended_action="Review source record before including this case in inference",
    )


def _alert_to_row(alert: QualityAlert) -> QualityAlertRow:
    return QualityAlertRow(
        alert_id=alert.alert_id,
        severity=alert.severity,
        case_id=alert.case_id,
        issue=alert.issue,
        timestamp=alert.timestamp,
        recommended_action=alert.recommended_action,
    )


def _inference_from_row(row: InferenceTraceRow) -> InferenceResult:
    from eosp.core.models import ParameterEstimate, TriggerType

    return InferenceResult(
        version=row.version,
        timestamp=row.timestamp,
        trigger=TriggerType(row.trigger_type),
        n_cases=row.n_cases,
        parameters={
            name: ParameterEstimate(**estimate)
            for name, estimate in row.posterior_summary.items()
        },
        diagnostics=row.diagnostics,
        posterior_download_url=row.posterior_s3_path,
    )
