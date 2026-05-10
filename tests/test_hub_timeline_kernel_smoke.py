from collections import Counter
from datetime import date, timedelta

import numpy as np

from eosp.core.models import CaseRecord, CaseStatus, LabResult, ObservationKind
from eosp.core.seed_data import CASES
from eosp.services.hub_timeline.config import HubTimelineSimulatorConfig
from eosp.services.hub_timeline.kernel import WorldGraphBundle, simulate_trajectory


def _case(airport: str, onset: date) -> CaseRecord:
    b = CASES[0]
    return CaseRecord(
        case_id=b.case_id,
        patient_identifier="solo",
        symptom_onset_date=onset,
        location_country="ZA",
        location_airport_code=airport,
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.NOT_TESTED,
        data_source="test",
        ingestion_timestamp=b.ingestion_timestamp,
        record_updated_at=b.record_updated_at,
        validation_score=0.9,
        observation_kind=ObservationKind.INDIVIDUAL,
    )


def test_kernel_secondaries_suppressed_when_transmission_zero(monkeypatch):
    monkeypatch.setattr(
        "eosp.services.hub_timeline.kernel.transmission_probability",
        lambda *a, **k: 0.0,
    )
    onset = date(2026, 1, 1)
    c = _case("HUB", onset)
    world = WorldGraphBundle(
        allowed=frozenset({"HUB"}),
        outbound={"HUB": {}},
        dest_weights=Counter({"HUB": 1}),
        iata_to_country={"HUB": "ZA"},
    )
    cfg = HubTimelineSimulatorConfig(horizon_days=3, mu_stay=2.0, mu_travel=2.0, mu_onset_burst=1.0, r_nb=100.0)
    days = [onset + timedelta(days=k) for k in range(3)]
    rng = np.random.default_rng(1)
    inf = {
        "p_transmit": (0.5, 0.01),
        "h2h_multiplier": (1.0, 0.1),
        "cfr": (0.01, 0.001),
        "incubation": (8.0, 1.0),
    }
    traj = simulate_trajectory([c], inference_params=inf, world=world, config=cfg, rng=rng, sim_days=days)
    assert traj.global_cases.max() < 15
