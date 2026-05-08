import numpy as np
import pytest

from eosp.services.metapop_seed import build_initial_metapop_state
from eosp.services.mobility import MobilitySchedule


def test_build_initial_metapop_state_maps_jnb_flight():
    schedule = MobilitySchedule(
        patch_ids=("NSEED", "JNB"),
        n_days=1,
        day_flows=(dict(),),
        total_outflow_per_patch_day=np.zeros((1, 2), dtype=np.float64),
    )
    ship_mass = 100.0
    n_pax = 200.0
    network_spec = {
        "flights": [
            {"destination": "ZA_JNB", "n_passengers": n_pax},
        ]
    }
    init_s, init_e, init_i, init_r = build_initial_metapop_state(
        schedule=schedule,
        network_spec=network_spec,
        ship_outbreak_mass=ship_mass,
    )

    idx_n = 0
    idx_j = 1
    assert init_e[idx_n] == pytest.approx(0.8 * ship_mass)
    assert init_i[idx_n] == pytest.approx(0.2 * ship_mass)
    assert init_e[idx_j] == pytest.approx(0.15 * n_pax)
    assert init_i[idx_j] == pytest.approx(0.05 * n_pax)
    assert init_s[idx_n] == pytest.approx(10_000.0 - ship_mass)
    assert init_s[idx_j] == pytest.approx(10_000.0 - 0.2 * n_pax)
    assert np.all(init_r == 0.0)
    for k in range(2):
        assert init_s[k] + init_e[k] + init_i[k] + init_r[k] == pytest.approx(
            10_000.0
        )
