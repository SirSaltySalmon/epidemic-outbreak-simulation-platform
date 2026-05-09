"""Cohort observations, WHO extract, and person-equivalent inference inputs."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import numpy as np

from eosp.core.case_statistics import case_summary_person_totals, total_cohort_persons
from eosp.core.models import (
    CaseRecord,
    CaseStatus,
    LabResult,
    ObservationKind,
)
from eosp.core.repository import empty_repository
from eosp.core.seed_data import CASES
from eosp.services.inference import daily_onsets_from_cases
from eosp.services.who_don_extract import extract_who_don_cohort_payloads
from eosp.services.who_don_ingest import ingest_who_don_from_html


def test_extract_who_don_finds_confirmed_and_country() -> None:
    html = """
    <html><main>
    <h1>Outbreak</h1>
    <p>On 4 May 2026, 8 laboratory-confirmed cases were reported in South Africa.</p>
    <p>3 deaths have been recorded.</p>
    </main></html>
    """
    rows = extract_who_don_cohort_payloads(html, feed_key="fk1", report_fallback=date(2026, 1, 1))
    assert len(rows) == 1
    p = rows[0]
    assert p.observation_kind == ObservationKind.COHORT
    assert p.cohort_size == 8
    assert p.cohort_deaths == 3
    assert p.location_country == "ZA"
    assert p.external_observation_key == "fk1:aggregate"


def test_case_summary_sums_cohort_sizes() -> None:
    cohort = CaseRecord(
        case_id=uuid4(),
        patient_identifier="x",
        symptom_onset_date=date(2026, 5, 1),
        location_country="ZA",
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.NOT_TESTED,
        data_source="WHO_DON",
        ingestion_timestamp=CASES[0].ingestion_timestamp,
        record_updated_at=CASES[0].record_updated_at,
        validation_score=0.9,
        observation_kind=ObservationKind.COHORT,
        cohort_size=5,
        cohort_deaths=2,
    )
    indiv = CASES[0]
    c, s, d, src = case_summary_person_totals([cohort, indiv])
    assert c == 5 + 1
    assert d == 2 + 1


def test_daily_onsets_sums_cohort_size() -> None:
    d0 = date(2026, 5, 1)
    cases = [
        CaseRecord(
            case_id=uuid4(),
            patient_identifier="c",
            symptom_onset_date=d0,
            location_country="ZA",
            confirmed_or_suspected=CaseStatus.CONFIRMED,
            lab_test_result=LabResult.NOT_TESTED,
            data_source="WHO_DON",
            ingestion_timestamp=CASES[0].ingestion_timestamp,
            record_updated_at=CASES[0].record_updated_at,
            validation_score=0.9,
            observation_kind=ObservationKind.COHORT,
            cohort_size=4,
        ),
    ]
    counts = daily_onsets_from_cases(cases, d0, n_days=3)
    assert counts[0] == 4
    assert int(np.sum(counts)) == 4


def test_upsert_idempotent_in_memory() -> None:
    repo = empty_repository()
    html = "<main>3 confirmed cases in Spain on 10 june 2026</main>"
    assert ingest_who_don_from_html(repo, html, feed_key="ev") is True
    assert total_cohort_persons(repo.list_cases()) == 3
    assert ingest_who_don_from_html(repo, html, feed_key="ev") is False
    assert len(repo.list_cases()) == 1


def test_upsert_updates_counts() -> None:
    repo = empty_repository()
    ingest_who_don_from_html(repo, "<main>3 confirmed cases in Spain</main>", feed_key="ev")
    assert ingest_who_don_from_html(repo, "<main>5 confirmed cases in Spain</main>", feed_key="ev") is True
    assert total_cohort_persons(repo.list_cases()) == 5
