from uuid import uuid4

from eosp.core.db import create_session_factory
from eosp.core.seed_data import ALERTS, CASES, INFERENCES, VALIDATIONS
from eosp.core.tables import (
    CaseRecordHistoryRow,
    CaseRecordRow,
    ForecastResultRow,
    InferenceTraceRow,
    QualityAlertRow,
    UserActionRow,
    ValidationResultRow,
)


def main() -> None:
    session_factory = create_session_factory()
    if session_factory is None:
        raise SystemExit("EOSP_DATABASE_URL is not configured. Copy .env.example to .env first.")

    with session_factory() as session:
        for table in (
            QualityAlertRow,
            UserActionRow,
            ForecastResultRow,
            InferenceTraceRow,
            ValidationResultRow,
            CaseRecordHistoryRow,
            CaseRecordRow,
        ):
            session.query(table).delete()

        for case in CASES:
            session.add(
                CaseRecordRow(
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
                )
            )

        session.flush()

        for case in CASES:
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

        for validation in VALIDATIONS.values():
            session.add(
                ValidationResultRow(
                    validation_id=uuid4(),
                    case_id=validation.case_id,
                    checks_passed=validation.checks_passed,
                    issues=validation.issues,
                    quality_score=validation.quality_score,
                    quarantine_status=validation.quarantine_status,
                )
            )

        for inference in INFERENCES:
            session.add(
                InferenceTraceRow(
                    trace_id=uuid4(),
                    version=inference.version,
                    timestamp=inference.timestamp,
                    trigger_type=inference.trigger.value,
                    n_cases=inference.n_cases,
                    posterior_s3_path=inference.posterior_download_url,
                    posterior_summary={
                        name: estimate.model_dump(mode="json")
                        for name, estimate in inference.parameters.items()
                    },
                    diagnostics=inference.diagnostics,
                    execution_time_seconds=1260.0,
                    data_quality_mean=0.84,
                    convergence_status=inference.diagnostics["convergence_status"],
                )
            )

        for alert in ALERTS:
            session.add(
                QualityAlertRow(
                    alert_id=alert.alert_id,
                    severity=alert.severity,
                    case_id=alert.case_id,
                    issue=alert.issue,
                    timestamp=alert.timestamp,
                    recommended_action=alert.recommended_action,
                )
            )

        session.commit()

    print("Seeded EOSP development data into PostgreSQL.")


if __name__ == "__main__":
    main()
