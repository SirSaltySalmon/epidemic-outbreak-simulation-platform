from datetime import date
from uuid import UUID, uuid4

from eosp.core.models import CaseRecord, CaseStatus, LabResult, ObservationKind
from eosp.core.seed_data import CASES
from eosp.services.hub_timeline.timeline import compute_simulation_calendar, eligible_hub_cases, eligible_individual_cases


def _individual(
    case_id: UUID,
    onset: date,
    airport: str | None,
    patient: str = "p",
) -> CaseRecord:
    base = CASES[0]
    return CaseRecord(
        case_id=case_id,
        patient_identifier=patient,
        symptom_onset_date=onset,
        location_country="ZA",
        location_airport_code=airport,
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.NOT_TESTED,
        data_source="test",
        ingestion_timestamp=base.ingestion_timestamp,
        record_updated_at=base.record_updated_at,
        validation_score=0.9,
        observation_kind=ObservationKind.INDIVIDUAL,
    )


def test_anchor_skips_earliest_symptom_when_flag_set():
    early_id = uuid4()
    cases = [
        _individual(early_id, date(2026, 4, 6), "JNB", "first"),
        _individual(uuid4(), date(2026, 4, 24), "JNB", "second"),
    ]
    allowed = frozenset({"JNB"})
    elig = eligible_hub_cases(
        cases, allowed_iatas=allowed, index_case_id=None, skip_earliest_symptom_case=True
    )
    anchor, days = compute_simulation_calendar(elig, horizon_days=30)
    assert anchor == date(2026, 4, 24)
    assert early_id not in {c.case_id for c in elig}


def test_anchor_skips_index_case_for_anchor_only():
    index_id = uuid4()
    cases = [
        _individual(index_id, date(2026, 5, 1), "JNB", "index"),
        _individual(uuid4(), date(2026, 5, 3), "JNB", "a"),
        _individual(uuid4(), date(2026, 5, 5), "CPT", "b"),
    ]
    allowed = frozenset({"JNB", "CPT"})
    elig = eligible_individual_cases(cases, allowed_iatas=allowed, index_case_id=index_id)
    anchor, days = compute_simulation_calendar(elig, horizon_days=30)
    assert anchor == date(2026, 5, 3)
    assert len(days) == 30
    assert days[0] == anchor
    assert days[-1] == date(2026, 6, 1)


def test_eligible_drops_unknown_airport():
    e = eligible_individual_cases(
        [_individual(uuid4(), date(2026, 5, 1), "XXX")],
        allowed_iatas=frozenset({"JNB"}),
        index_case_id=None,
    )
    assert e == []


def test_eligible_hub_cases_includes_cohort_rows():
    base = CASES[0]
    cohort = CaseRecord(
        case_id=uuid4(),
        patient_identifier="cohort1",
        symptom_onset_date=date(2026, 5, 2),
        location_country="ZA",
        location_airport_code="JNB",
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.NOT_TESTED,
        data_source="test",
        ingestion_timestamp=base.ingestion_timestamp,
        record_updated_at=base.record_updated_at,
        validation_score=0.9,
        observation_kind=ObservationKind.COHORT,
        cohort_size=5,
    )
    allowed = frozenset({"JNB"})
    out = eligible_hub_cases([cohort], allowed_iatas=allowed, index_case_id=None)
    assert len(out) == 1
    anchor, days = compute_simulation_calendar(out, horizon_days=7)
    assert anchor == date(2026, 5, 2)
    assert len(days) == 7
