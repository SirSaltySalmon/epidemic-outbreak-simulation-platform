from datetime import date

import numpy as np

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
