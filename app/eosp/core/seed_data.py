from datetime import date, datetime, timezone
from uuid import UUID

from eosp.core.models import (
    CaseRecord,
    CaseStatus,
    InferenceResult,
    LabResult,
    ParameterEstimate,
    QualityAlert,
    TriggerType,
    ValidationResult,
)
from eosp.core.repository import InMemoryRepository
from eosp.services.validation import validate_case


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


CASES = [
    CaseRecord(
        case_id=UUID("20260406-0000-4000-8000-000000000001"),
        patient_identifier="anon_p0001",
        symptom_onset_date=date(2026, 4, 6),
        hospitalization_date=date(2026, 4, 11),
        death_date=date(2026, 4, 14),
        location_country="ZA",
        location_airport_code="JNB",
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.PCR_POSITIVE,
        contacts=[{"id": "anon_p0002", "type": "household"}],
        data_source="WHO_DON",
        ingestion_timestamp=_utc("2026-05-06T08:00:00Z"),
        record_updated_at=_utc("2026-05-06T08:00:00Z"),
        validation_score=0.94,
    ),
    CaseRecord(
        case_id=UUID("20260409-0000-4000-8000-000000000002"),
        patient_identifier="anon_p0002",
        symptom_onset_date=date(2026, 4, 9),
        hospitalization_date=date(2026, 4, 14),
        death_date=None,
        location_country="ZA",
        location_airport_code="JNB",
        confirmed_or_suspected=CaseStatus.SUSPECTED,
        lab_test_result=LabResult.NOT_TESTED,
        contacts=[{"id": "anon_p0001", "type": "household"}],
        data_source="Manual_Form",
        ingestion_timestamp=_utc("2026-05-06T08:30:00Z"),
        record_updated_at=_utc("2026-05-06T08:30:00Z"),
        validation_score=0.82,
    ),
    CaseRecord(
        case_id=UUID("20260414-0000-4000-8000-000000000003"),
        patient_identifier="anon_p0003",
        symptom_onset_date=date(2026, 4, 14),
        hospitalization_date=date(2026, 4, 18),
        death_date=date(2026, 4, 23),
        location_country="CH",
        location_airport_code="ZRH",
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.PCR_POSITIVE,
        contacts=[],
        data_source="WHO_DON",
        ingestion_timestamp=_utc("2026-05-06T09:00:00Z"),
        record_updated_at=_utc("2026-05-06T09:00:00Z"),
        validation_score=0.91,
    ),
    CaseRecord(
        case_id=UUID("20260418-0000-4000-8000-000000000004"),
        patient_identifier="anon_p0004",
        symptom_onset_date=date(2026, 4, 18),
        hospitalization_date=date(2026, 4, 21),
        death_date=None,
        location_country="NL",
        location_airport_code="AMS",
        confirmed_or_suspected=CaseStatus.SUSPECTED,
        lab_test_result=LabResult.NOT_TESTED,
        contacts=[],
        data_source="Contact_Trace_DB",
        ingestion_timestamp=_utc("2026-05-06T10:00:00Z"),
        record_updated_at=_utc("2026-05-06T10:00:00Z"),
        validation_score=0.79,
    ),
    CaseRecord(
        case_id=UUID("20260421-0000-4000-8000-000000000005"),
        patient_identifier="anon_p0005",
        symptom_onset_date=date(2026, 4, 21),
        hospitalization_date=date(2026, 4, 24),
        death_date=date(2026, 4, 28),
        location_country="QA",
        location_airport_code="DOH",
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.PCR_POSITIVE,
        contacts=[],
        data_source="WHO_DON",
        ingestion_timestamp=_utc("2026-05-06T11:00:00Z"),
        record_updated_at=_utc("2026-05-06T11:00:00Z"),
        validation_score=0.93,
    ),
    CaseRecord(
        case_id=UUID("20260423-0000-4000-8000-000000000006"),
        patient_identifier="anon_p0006",
        symptom_onset_date=date(2026, 4, 23),
        hospitalization_date=date(2026, 4, 26),
        death_date=None,
        location_country="QA",
        location_airport_code="DOH",
        confirmed_or_suspected=CaseStatus.SUSPECTED,
        lab_test_result=LabResult.NOT_TESTED,
        contacts=[],
        data_source="Manual_Form",
        ingestion_timestamp=_utc("2026-05-06T12:00:00Z"),
        record_updated_at=_utc("2026-05-06T12:00:00Z"),
        validation_score=0.76,
    ),
    CaseRecord(
        case_id=UUID("20260425-0000-4000-8000-000000000007"),
        patient_identifier="anon_p0007",
        symptom_onset_date=date(2026, 4, 25),
        hospitalization_date=date(2026, 4, 29),
        death_date=None,
        location_country="NL",
        location_airport_code="AMS",
        confirmed_or_suspected=CaseStatus.SUSPECTED,
        lab_test_result=LabResult.NOT_TESTED,
        contacts=[],
        data_source="Manual_Form",
        ingestion_timestamp=_utc("2026-05-06T13:00:00Z"),
        record_updated_at=_utc("2026-05-06T13:00:00Z"),
        validation_score=0.74,
    ),
    CaseRecord(
        case_id=UUID("20260428-0000-4000-8000-000000000008"),
        patient_identifier="anon_p0008",
        symptom_onset_date=date(2026, 4, 28),
        hospitalization_date=date(2026, 5, 2),
        death_date=None,
        location_country="QA",
        location_airport_code="DOH",
        confirmed_or_suspected=CaseStatus.SUSPECTED,
        lab_test_result=LabResult.NOT_TESTED,
        contacts=[],
        data_source="WHO_DON",
        ingestion_timestamp=_utc("2026-05-07T14:32:00Z"),
        record_updated_at=_utc("2026-05-07T14:32:00Z"),
        validation_score=0.86,
    ),
]

VALIDATIONS = {case.case_id: validate_case(case, CASES) for case in CASES}

INFERENCES = [
    InferenceResult(
        version="v1.4.2-20260507T143200Z",
        timestamp=_utc("2026-05-07T14:32:00Z"),
        trigger=TriggerType.NEW_CASE,
        n_cases=8,
        parameters={
            "p_transmit": ParameterEstimate(mean=0.089, std=0.034, ci_95=(0.031, 0.162)),
            "contacts_daily": ParameterEstimate(mean=2.4, std=0.8, ci_95=(1.2, 4.5)),
            "incubation": ParameterEstimate(mean=8.2, std=2.1, ci_95=(4.4, 13.1)),
            "h2h_multiplier": ParameterEstimate(mean=1.18, std=0.31, ci_95=(0.72, 1.91)),
        },
        diagnostics={
            "rhat": {"p_transmit": 1.006, "contacts_daily": 1.008, "incubation": 1.004, "h2h_multiplier": 1.007},
            "effective_sample_size": {"p_transmit": 1280, "contacts_daily": 910, "incubation": 1560, "h2h_multiplier": 875},
            "divergences": 23,
            "convergence_status": "CONVERGED",
        },
        posterior_download_url="s3://eosp-data/posteriors/v1.4.2-20260507T143200Z.nc",
    ),
    InferenceResult(
        version="v1.4.1-20260507T020000Z",
        timestamp=_utc("2026-05-07T02:00:00Z"),
        trigger=TriggerType.CRON,
        n_cases=7,
        parameters={
            "p_transmit": ParameterEstimate(mean=0.078, std=0.035, ci_95=(0.025, 0.151)),
            "contacts_daily": ParameterEstimate(mean=2.3, std=0.9, ci_95=(1.0, 4.6)),
            "incubation": ParameterEstimate(mean=8.4, std=2.3, ci_95=(4.2, 13.8)),
            "h2h_multiplier": ParameterEstimate(mean=1.12, std=0.34, ci_95=(0.68, 1.88)),
        },
        diagnostics={
            "rhat": {"p_transmit": 1.009, "contacts_daily": 1.008, "incubation": 1.005, "h2h_multiplier": 1.006},
            "effective_sample_size": {"p_transmit": 1030, "contacts_daily": 840, "incubation": 1420, "h2h_multiplier": 790},
            "divergences": 29,
            "convergence_status": "CONVERGED",
        },
        posterior_download_url="s3://eosp-data/posteriors/v1.4.1-20260507T020000Z.nc",
    ),
]

ALERTS = [
    QualityAlert(
        alert_id="alert_20260507_001",
        severity="MEDIUM",
        case_id=CASES[6].case_id,
        issue="Validation score below admin review threshold",
        timestamp=_utc("2026-05-07T09:15:00Z"),
        recommended_action="Review source record and confirm lab status when available",
    )
]

repository = InMemoryRepository(cases=CASES, validations=VALIDATIONS, inferences=INFERENCES, alerts=ALERTS)

