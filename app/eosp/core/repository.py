from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID
from uuid import uuid4

from eosp.core.models import (
    CaseCreate,
    CaseRecord,
    CaseStatus,
    ForecastResponse,
    ForecastRunItem,
    ForecastRunResponse,
    InferenceResult,
    QualityAlert,
    ValidationResult,
)
from eosp.core.tables import (
    CaseRecordHistoryRow,
    CaseRecordRow,
    ForecastResultRow,
    InferenceTraceRow,
    QualityAlertRow,
    UserActionRow,
    ValidationResultRow,
)
from eosp.services.validation import validate_case


@dataclass
class InMemoryRepository:
    cases: list[CaseRecord]
    validations: dict[UUID, ValidationResult]
    inferences: list[InferenceResult]
    alerts: list[QualityAlert]
    forecast_runs: list[ForecastRunItem] | None = None
    forecast_cache: dict[str, ForecastResponse] = field(default_factory=dict)

    def case_summary(self):
        confirmed = sum(1 for case in self.cases if case.confirmed_or_suspected == CaseStatus.CONFIRMED)
        suspected = sum(1 for case in self.cases if case.confirmed_or_suspected == CaseStatus.SUSPECTED)
        deaths = sum(1 for case in self.cases if case.death_date is not None)
        last_updated = max((case.ingestion_timestamp for case in self.cases), default=datetime.now(UTC))
        sources = Counter(case.data_source for case in self.cases)
        return confirmed, suspected, deaths, last_updated, dict(sources)

    def list_cases(self, country: str | None = None, status: CaseStatus | None = None) -> list[CaseRecord]:
        records = self.cases
        if country:
            records = [case for case in records if case.location_country.upper() == country.upper()]
        if status:
            records = [case for case in records if case.confirmed_or_suspected == status]
        return sorted(records, key=lambda case: case.symptom_onset_date)

    def get_validation(self, case_id: UUID) -> ValidationResult | None:
        return self.validations.get(case_id)

    def create_case(self, payload: CaseCreate) -> tuple[CaseRecord, ValidationResult]:
        now = datetime.now(UTC)
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
            ingestion_timestamp=payload.ingestion_timestamp or now,
            validation_score=0,
            updated_by=payload.updated_by,
            updated_reason=payload.updated_reason,
        )
        validation = validate_case(case, [*self.cases, case])
        case.validation_score = validation.quality_score
        self.cases.append(case)
        self.validations[case.case_id] = validation
        if validation.quality_score < 0.75:
            self.alerts.append(_alert_for_validation(case, validation))
        return case, validation

    def latest_inference(self) -> InferenceResult:
        return self.inferences[0]

    def get_inference(self, version: str) -> InferenceResult | None:
        if version == "latest":
            return self.latest_inference()
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

    def update_inference(self, inference: InferenceResult) -> None:
        self.inferences.insert(0, inference)


@dataclass
class SqlRepository:
    session_factory: object

    def case_summary(self):
        with self.session_factory() as session:
            records = session.query(CaseRecordRow).filter(CaseRecordRow.active.is_(True)).all()
            confirmed = sum(1 for case in records if case.confirmed_or_suspected == CaseStatus.CONFIRMED.value)
            suspected = sum(1 for case in records if case.confirmed_or_suspected == CaseStatus.SUSPECTED.value)
            deaths = sum(1 for case in records if case.death_date is not None)
            last_updated = max((case.ingestion_timestamp for case in records), default=datetime.now(UTC))
            sources = Counter(case.data_source for case in records)
            return confirmed, suspected, deaths, last_updated, dict(sources)

    def list_cases(self, country: str | None = None, status: CaseStatus | None = None) -> list[CaseRecord]:
        with self.session_factory() as session:
            query = session.query(CaseRecordRow).filter(CaseRecordRow.active.is_(True))
            if country:
                query = query.filter(CaseRecordRow.location_country == country.upper())
            if status:
                query = query.filter(CaseRecordRow.confirmed_or_suspected == status.value)
            rows = query.order_by(CaseRecordRow.symptom_onset_date).all()
            return [_case_from_row(row) for row in rows]

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
                ingestion_timestamp=payload.ingestion_timestamp or now,
                validation_score=0,
                updated_by=payload.updated_by,
                updated_reason=payload.updated_reason,
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
            row = (
                session.query(ForecastResultRow)
                .filter(
                    ForecastResultRow.model_version == model_version,
                    ForecastResultRow.scenario == scenario,
                )
                .order_by(ForecastResultRow.generated_timestamp.desc())
                .first()
            )
            payload = response.model_dump(mode="json")
            if row is None:
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
            else:
                row.forecast_json = payload
                row.n_simulations = int(response.metadata.get("n_simulations", row.n_simulations))
                row.execution_time_seconds = float(response.metadata.get("execution_time_seconds", row.execution_time_seconds))
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


def _case_from_row(row: CaseRecordRow) -> CaseRecord:
    return CaseRecord(
        case_id=row.case_id,
        patient_identifier=row.patient_identifier,
        symptom_onset_date=row.symptom_onset_date,
        hospitalization_date=row.hospitalization_date,
        death_date=row.death_date,
        location_country=row.location_country,
        location_airport_code=row.location_airport_code,
        confirmed_or_suspected=CaseStatus(row.confirmed_or_suspected),
        lab_test_result=row.lab_test_result,
        contacts=row.contacts,
        data_source=row.data_source,
        ingestion_timestamp=row.ingestion_timestamp,
        validation_score=row.validation_score,
        version=row.version,
        updated_by=row.updated_by,
        updated_reason=row.updated_reason,
    )


def _case_to_row(case: CaseRecord) -> CaseRecordRow:
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
