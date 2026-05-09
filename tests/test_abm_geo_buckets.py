import numpy as np

from eosp.services.abm import SeedState, simulate_trajectory
from eosp.services.network import build_default_network


def test_trajectory_exposes_bucket_infectious_I_shape():
    net = build_default_network(n_days=5)
    n_agents = net.n_agents
    seed = SeedState(
        exposed=[],
        infectious=list(range(min(3, n_agents))),
        recovered=[],
        deceased=[],
    )
    traj = simulate_trajectory(
        net,
        params={
            "p_transmit": 0.01,
            "contacts_daily": 2.0,
            "incubation_mean": 5.0,
            "h2h_multiplier": 1.0,
            "cfr": 0.1,
        },
        seed=seed,
        n_days=5,
        rng=np.random.default_rng(42),
    )
    assert traj.bucket_infectious_I is not None
    assert traj.bucket_infectious_I.shape == (6, len(traj.geo_bucket_labels))
