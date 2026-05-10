import numpy as np

from eosp.services.hub_timeline.kernel import HubTrajectorySnapshot
from eosp.services.hub_timeline.replay import build_replay_geo


def _fake_traj(days_n: int, hub: str) -> HubTrajectorySnapshot:
    from datetime import date

    dates = [date(2026, 1, 1 + i) for i in range(days_n)]
    cum = np.ones((days_n, 1)) * 2.0
    a = np.zeros((days_n, 1))
    a[0, 0] = 1.0
    return HubTrajectorySnapshot(
        dates=dates,
        hubs=[hub],
        hub_index={hub: 0},
        cumulative_infected=cum,
        infectious_A=a,
        peak_unweighted_PI=np.array([1.0]),
        global_cases=np.linspace(1, 3, days_n),
        global_deaths=np.zeros(days_n),
    )


def test_replay_omits_all_zero_days():
    t = _fake_traj(3, "JFK")
    out = build_replay_geo([t], anchor_date_str="2026-01-01")
    assert out["schema_version"] == "eosp_geo_replay_1"
    assert any("JFK" in d.get("airports", {}) for d in out["days"])
