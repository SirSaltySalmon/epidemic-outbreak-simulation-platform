import numpy as np

from eosp.services import abm
from eosp.services.abm import SeedState, simulate_trajectory
from eosp.services.network import build_default_network


PARAMS = {
    "p_transmit": 0.12,
    "contacts_daily": 2.5,
    "incubation_mean": 8.0,
    "h2h_multiplier": 1.2,
    "cfr": 0.35,
}


def test_simulate_trajectory_returns_consistent_shapes():
    network = build_default_network()
    rng = np.random.default_rng(42)
    seed = SeedState(exposed=[10, 11, 12], infectious=[0, 1], recovered=[], deceased=[])
    trajectory = simulate_trajectory(network, PARAMS, seed=seed, n_days=14, rng=rng)

    assert trajectory.daily_counts.shape == (15, abm.N_STATES)
    assert trajectory.cumulative_cases.shape == (15,)
    assert trajectory.cumulative_deaths.shape == (15,)
    assert trajectory.new_cases_per_day.shape == (15,)


def test_simulate_trajectory_grows_caseload_with_high_p_transmit():
    network = build_default_network()
    rng = np.random.default_rng(7)
    seed = SeedState(exposed=[], infectious=[0, 1, 2, 3], recovered=[], deceased=[])
    high_params = {**PARAMS, "p_transmit": 0.45, "h2h_multiplier": 2.0}
    trajectory = simulate_trajectory(network, high_params, seed=seed, n_days=14, rng=rng)

    assert trajectory.cumulative_cases[-1] >= trajectory.cumulative_cases[0]
    assert trajectory.daily_counts[-1, 3] + trajectory.daily_counts[-1, 4] > 0  # some R or D


def test_no_seeded_infection_yields_zero_growth():
    network = build_default_network()
    rng = np.random.default_rng(123)
    seed = SeedState(exposed=[], infectious=[], recovered=[], deceased=[])
    trajectory = simulate_trajectory(network, PARAMS, seed=seed, n_days=14, rng=rng)

    assert trajectory.cumulative_cases.max() == 0
    assert trajectory.new_cases_per_day.sum() == 0
