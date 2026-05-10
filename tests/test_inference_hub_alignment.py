from datetime import UTC, datetime, timedelta

import pytest

from eosp.core.compute_config import InferenceConfig
from eosp.core.models import InferenceResult, ParameterEstimate, TriggerType
from eosp.core.seed_data import CASES
from eosp.services.hub_timeline.config import HubTimelineSimulatorConfig, default_hub_timeline_config
from eosp.services.hub_timeline.ensemble import (
    _inference_param_draws,
    _scenario_config,
    run_hub_timeline_forecast,
)
from eosp.services.hub_timeline.inference_surrogate import build_hub_surrogate_features
from eosp.services.inference import run_inference
from eosp.services.scenarios import get_scenario

numpyro = pytest.importorskip("numpyro")
jax = pytest.importorskip("jax")


def test_default_inference_config_uses_low_casual_contact_prior():
    cfg = InferenceConfig()
    assert cfg.inference_model == "hub_surrogate_v2"
    assert cfg.p_transmit_prior_alpha == 1.0
    assert cfg.p_transmit_prior_beta == 80.0
    assert cfg.p_transmit_prior_alpha / (cfg.p_transmit_prior_alpha + cfg.p_transmit_prior_beta) < 0.013
    assert cfg.h2h_log_sigma == 0.25
    assert cfg.cfr_prior_alpha == 2.0
    assert cfg.cfr_prior_beta == 50.0


def _inference_with_contacts_daily(value: float) -> InferenceResult:
    return InferenceResult(
        version="v-test",
        timestamp=datetime(2026, 5, 10, tzinfo=UTC),
        trigger=TriggerType.MANUAL,
        n_cases=8,
        parameters={
            "p_transmit": ParameterEstimate(mean=0.012, std=0.002, ci_95=(0.004, 0.025)),
            "contacts_daily": ParameterEstimate(mean=value, std=0.1, ci_95=(value - 0.1, value + 0.1)),
            "incubation": ParameterEstimate(mean=8.0, std=1.0, ci_95=(6.0, 10.0)),
            "h2h_multiplier": ParameterEstimate(mean=1.0, std=0.1, ci_95=(0.8, 1.2)),
            "cfr": ParameterEstimate(mean=0.03, std=0.01, ci_95=(0.01, 0.06)),
        },
        diagnostics={},
        posterior_download_url="local://test",
    )


def test_inferred_contacts_daily_does_not_rescale_hub_contact_means():
    cfg = _scenario_config(get_scenario("baseline"), default_hub_timeline_config(), _inference_with_contacts_daily(20.0))
    assert cfg.mu_travel == 20.0
    assert cfg.mu_stay == 5.0
    assert cfg.mu_onset_burst == 20.0


def test_surrogate_features_include_onset_bursts_and_no_pre_onset_fixed_load():
    cases = [CASES[0]]
    cfg = HubTimelineSimulatorConfig(horizon_days=3, mu_stay=5.0, mu_onset_burst=20.0)
    days = [
        CASES[0].symptom_onset_date - timedelta(days=1),
        CASES[0].symptom_onset_date,
        CASES[0].symptom_onset_date + timedelta(days=1),
    ]

    features = build_hub_surrogate_features(cases, sim_days=days, config=cfg)

    assert features.observed_onsets.tolist() == [0.0, 1.0, 0.0]
    assert features.fixed_contact_mu_by_day.tolist()[0] == 0.0
    assert features.fixed_contact_mu_by_day.tolist()[1] >= 25.0
    assert features.fixed_load_by_day.tolist()[0] == 0.0
    assert features.fixed_load_by_day.tolist()[1] == 1.0


def test_inference_draws_ignore_removed_contacts_daily_parameter():
    inf = _inference_with_contacts_daily(30.0)
    spec = get_scenario("baseline")
    cfg = default_hub_timeline_config()
    draws = _inference_param_draws(inf, spec, cfg)

    assert "contacts_daily" not in draws
    assert draws["p_transmit"][0] == 0.012


def test_low_transmission_baseline_forecast_stays_bounded():
    inf = _inference_with_contacts_daily(4.0).model_copy(
        update={
            "parameters": {
                "p_transmit": ParameterEstimate(mean=0.012, std=0.0, ci_95=(0.012, 0.012)),
                "incubation": ParameterEstimate(mean=8.0, std=0.0, ci_95=(8.0, 8.0)),
                "h2h_multiplier": ParameterEstimate(mean=1.0, std=0.0, ci_95=(1.0, 1.0)),
                "cfr": ParameterEstimate(mean=0.03, std=0.0, ci_95=(0.03, 0.03)),
            }
        }
    )
    forecast = run_hub_timeline_forecast(
        scenario_name="baseline",
        cases=list(CASES),
        inference=inf,
        n_simulations=3,
        rng_seed=20260507,
        max_workers=1,
    )

    assert forecast.forecast[13].cases_cumulative["median"] < 80
    assert forecast.forecast[-1].cases_cumulative["median"] < 250


@pytest.mark.slow
def test_short_nuts_posterior_keeps_casual_contact_transmission_low(tmp_path):
    config = InferenceConfig(
        num_warmup=40,
        num_samples=40,
        num_chains=1,
        persist_netcdf=False,
        posteriors_dir=tmp_path,
    )
    artifacts = run_inference(cases=list(CASES), network=None, config=config)
    assert artifacts.result.parameters["p_transmit"].mean < 0.04
