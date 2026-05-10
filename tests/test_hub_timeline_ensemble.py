from datetime import date
from uuid import uuid4

import numpy as np

from eosp.core.models import CaseRecord, CaseStatus, LabResult, ObservationKind
from eosp.core.seed_data import CASES, INFERENCES
from eosp.services.hub_timeline.ensemble import run_hub_timeline_forecast
from eosp.services.hub_timeline.kernel import HubTrajectorySnapshot


def test_hub_timeline_ensemble_smoke(monkeypatch):
    def fake_sim(*args, **kwargs):
        sim_days: list[date] = kwargs["sim_days"]
        n = len(sim_days)
        hub = "JNB"
        return HubTrajectorySnapshot(
            dates=list(sim_days),
            hubs=[hub],
            hub_index={hub: 0},
            cumulative_infected=np.ones((n, 1)),
            infectious_A=np.zeros((n, 1)),
            peak_unweighted_PI=np.array([1.0]),
            global_cases=np.linspace(1, 2, n),
            global_deaths=np.zeros(n),
        )

    monkeypatch.setattr("eosp.services.hub_timeline.ensemble.simulate_trajectory", fake_sim)

    fr = run_hub_timeline_forecast(
        scenario_name="baseline",
        cases=list(CASES),
        inference=INFERENCES[0],
        n_simulations=3,
        rng_seed=42,
        index_case_id=None,
        max_workers=2,
    )
    assert fr.metadata.get("engine") == "hub_timeline_monte_carlo"
    assert fr.metadata.get("geo_forecast")
    assert fr.metadata.get("replay_geo", {}).get("schema_version") == "eosp_geo_replay_1"
    assert len(fr.forecast) == 30
    assert fr.forecast[-1].cases_cumulative["median"] >= 0


def _two_jnb_cases():
    base = CASES[0]

    def row(onset: date, pid: str) -> CaseRecord:
        return CaseRecord(
            case_id=uuid4(),
            patient_identifier=pid,
            symptom_onset_date=onset,
            location_country="ZA",
            location_airport_code="JNB",
            confirmed_or_suspected=CaseStatus.CONFIRMED,
            lab_test_result=LabResult.NOT_TESTED,
            data_source="test",
            ingestion_timestamp=base.ingestion_timestamp,
            record_updated_at=base.record_updated_at,
            validation_score=0.9,
            observation_kind=ObservationKind.INDIVIDUAL,
        )

    return [row(date(2026, 4, 6), "first"), row(date(2026, 4, 24), "second")]


def test_anchor_skipped_case_adds_one_to_reported_global_cases_and_deaths(monkeypatch):
    """Cases/deaths removed from the sim for anchoring are added back to forecast totals."""

    def fake_sim(*args, **kwargs):
        sim_days: list[date] = kwargs["sim_days"]
        n = len(sim_days)
        hub = "JNB"
        return HubTrajectorySnapshot(
            dates=list(sim_days),
            hubs=[hub],
            hub_index={hub: 0},
            cumulative_infected=np.ones((n, 1)),
            infectious_A=np.zeros((n, 1)),
            peak_unweighted_PI=np.array([1.0]),
            global_cases=np.linspace(1, 2, n),
            global_deaths=np.zeros(n),
        )

    monkeypatch.setattr("eosp.services.hub_timeline.ensemble.simulate_trajectory", fake_sim)
    cases = _two_jnb_cases()
    common = dict(
        scenario_name="baseline",
        cases=cases,
        inference=INFERENCES[0],
        n_simulations=3,
        rng_seed=99,
        max_workers=2,
    )
    fr_skip = run_hub_timeline_forecast(**common, skip_earliest_symptom_case=True)
    fr_full = run_hub_timeline_forecast(**common, skip_earliest_symptom_case=False)
    assert fr_skip.metadata.get("hub_skipped_cases_added_to_totals") == 1
    assert fr_full.metadata.get("hub_skipped_cases_added_to_totals") == 0
    last_s = fr_skip.forecast[-1]
    last_f = fr_full.forecast[-1]
    assert last_s.cases_cumulative["median"] == last_f.cases_cumulative["median"] + 1
    assert last_s.deaths_cumulative["median"] == last_f.deaths_cumulative["median"] + 1
