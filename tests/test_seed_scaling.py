"""Proportional seed scaling to the contact cohort (N_pool)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from eosp.core.models import CaseRecord, CaseStatus, LabResult, ObservationKind
from eosp.services.abm import SeedState
from eosp.services.ensemble import seed_state_from_case_records
from eosp.services.network import ContactNetwork


def _tiny_cohort(n: int, iata: str = "JNB") -> ContactNetwork:
    meta = [
        {
            "id": f"a{i}",
            "role": "airport_contact",
            "cohort_agent": True,
            "home_iata": iata,
            "destination": f"ZA_{iata}",
        }
        for i in range(n)
    ]
    return ContactNetwork(n_agents=n, layers=[], node_metadata=meta, n_days=5)


def test_seed_scaling_maintains_pool_and_metadata():
    big = CaseRecord(
        case_id=uuid4(),
        patient_identifier="x",
        symptom_onset_date=date(2026, 1, 1),
        hospitalization_date=date(2026, 1, 3),
        death_date=None,
        location_country="ZA",
        location_airport_code="JNB",
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.PCR_POSITIVE,
        contacts=[],
        data_source="t",
        observation_kind=ObservationKind.COHORT,
        cohort_size=800,
        cohort_deaths=0,
        ingestion_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        record_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        validation_score=0.9,
    )
    net = _tiny_cohort(12)
    seed = seed_state_from_case_records(network=net, cases=[big], rng_seed=99)
    assert isinstance(seed, SeedState)
    n_seeded = len(seed.exposed) + len(seed.infectious) + len(seed.recovered) + len(seed.deceased)
    assert n_seeded == 12
    assert seed.seed_scaling is not None
    assert seed.seed_scaling.get("method") == "proportional_to_pool"
    assert seed.seed_scaling.get("n_pool") == 12
