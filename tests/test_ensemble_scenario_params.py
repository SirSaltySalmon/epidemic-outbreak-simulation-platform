"""Regression: scenario_params must affect ABM input draws (contacts_daily, etc.)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from eosp.core.models import InferenceResult, ParameterEstimate, TriggerType
from eosp.services.ensemble import _draw_parameter_samples, _resample_posterior


def _minimal_inference() -> InferenceResult:
    return InferenceResult(
        version="v-test",
        timestamp=datetime.now(UTC),
        trigger=TriggerType.MANUAL,
        n_cases=10,
        parameters={
            "p_transmit": ParameterEstimate(mean=0.08, std=0.02, ci_95=(0.05, 0.11)),
            "contacts_daily": ParameterEstimate(mean=2.4, std=0.5, ci_95=(1.5, 3.3)),
            "incubation": ParameterEstimate(mean=8.0, std=1.0, ci_95=(6.0, 10.0)),
            "h2h_multiplier": ParameterEstimate(mean=1.0, std=0.2, ci_95=(0.8, 1.2)),
            "cfr": ParameterEstimate(mean=0.4, std=0.05, ci_95=(0.35, 0.45)),
        },
        diagnostics={},
        posterior_download_url="local://test",
    )


def test_draw_parameter_samples_respects_scenario_contacts_daily() -> None:
    inf = _minimal_inference()
    base_params = {
        "p_transmit": 0.08,
        "contacts_daily": 2.4,
        "incubation_mean": 8.0,
        "h2h_multiplier": 1.0,
        "cfr": 0.4,
    }
    low_params = {**base_params, "contacts_daily": 1.0}
    s_base = _draw_parameter_samples(
        inference=inf,
        scenario_params=base_params,
        n_simulations=4000,
        rng_seed=7,
        posterior_samples=None,
    )
    s_low = _draw_parameter_samples(
        inference=inf,
        scenario_params=low_params,
        n_simulations=4000,
        rng_seed=7,
        posterior_samples=None,
    )
    assert np.mean(s_low["contacts_daily"]) < np.mean(s_base["contacts_daily"])
    assert abs(float(np.mean(s_low["contacts_daily"])) - 1.0) < 0.2


def test_resample_posterior_aligns_contacts_to_scenario_params() -> None:
    inf = _minimal_inference()
    n = 500
    posterior_samples = {
        "p_transmit": np.full(n, 0.08),
        "contacts_daily": np.full(n, 2.4),
        "incubation": np.full(n, 8.0),
        "h2h_multiplier": np.full(n, 1.0),
        "cfr": np.full(n, 0.4),
    }
    rng = np.random.default_rng(3)
    scenario_params = {
        "p_transmit": 0.08,
        "contacts_daily": 1.2,
        "incubation_mean": 8.0,
        "h2h_multiplier": 1.0,
        "cfr": 0.4,
    }
    drawn = _resample_posterior(posterior_samples, 200, rng, scenario_params, inf)
    assert abs(float(np.mean(drawn["contacts_daily"])) - 1.2) < 1e-6
