import numpy as np

from eosp.services.hub_timeline.agents import step_mobility_synthetic
from eosp.services.hub_timeline.config import HubTimelineSimulatorConfig


def test_away_phase_no_triangular_routing():
    rng = np.random.default_rng(123)
    cfg = HubTimelineSimulatorConfig(p_return_when_away=0.0, p_stay_when_at_home=0.5)
    outbound = {
        "JNB": {"CPT": 5},
        "CPT": {"JNB": 5},
    }
    cur, away = "CPT", True
    for _ in range(12):
        cur, away = step_mobility_synthetic(
            rng, iata_current=cur, iata_home="JNB", away=away, outbound=outbound, cfg=cfg
        )
        assert cur in ("JNB", "CPT")
