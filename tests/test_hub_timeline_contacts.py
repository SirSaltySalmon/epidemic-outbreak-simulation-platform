import numpy as np

from eosp.services.hub_timeline.contacts import draw_contact_count, transmission_probability


def test_nb_mean_matches_mu_for_many_draws():
    rng = np.random.default_rng(20260510)
    draws = [draw_contact_count(rng, 20.0, 10.0) for _ in range(5000)]
    m = float(np.mean(draws))
    assert 18.0 < m < 22.0


def test_nb_large_dispersion_looks_poissonish():
    rng = np.random.default_rng(1)
    draws_nb = [draw_contact_count(rng, 12.0, 1e10) for _ in range(2000)]
    rng2 = np.random.default_rng(1)
    draws_p = [int(rng2.poisson(12.0)) for _ in range(2000)]
    assert abs(np.mean(draws_nb) - np.mean(draws_p)) < 1.0


def test_transmission_prob_bounded():
    p = transmission_probability(0.2, 1.5, 10.0, alpha_load=0.08)
    assert 0.0 < p < 1.0
