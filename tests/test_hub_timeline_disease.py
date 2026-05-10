import numpy as np

from eosp.services.hub_timeline.disease_clock import sample_e_and_p_remaining, sample_weibull_duration


def test_duration_draws_positive():
    rng = np.random.default_rng(0)
    e, p = sample_e_and_p_remaining(
        rng,
        incubation_mean=8.0,
        gamma_shape=2.0,
        mean_scale=1.0,
        p_share_mean=0.35,
    )
    assert e >= 1 and p >= 1
    w = sample_weibull_duration(rng, mean=7.0, shape=1.5)
    assert w >= 1
