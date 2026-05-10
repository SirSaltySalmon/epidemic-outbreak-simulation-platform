import pytest

from eosp.core.compute_config import InferenceConfig
from eosp.core.seed_data import CASES
from eosp.services.inference import run_inference
from eosp.services.network import build_default_network


numpyro = pytest.importorskip("numpyro")
jax = pytest.importorskip("jax")


def test_run_inference_produces_summary_and_diagnostics(tmp_path):
    network = build_default_network()
    config = InferenceConfig(
        num_warmup=80,
        num_samples=80,
        num_chains=1,
        target_accept_prob=0.8,
        persist_netcdf=False,
        posteriors_dir=tmp_path,
    )
    artifacts = run_inference(cases=list(CASES), network=network, config=config)
    result = artifacts.result

    expected_params = {"p_transmit", "incubation", "h2h_multiplier", "cfr", "reporting_fraction", "background_onset_rate"}
    unexpected_params = {"contacts_daily"}
    assert unexpected_params.isdisjoint(result.parameters.keys())
    assert expected_params.issubset(result.parameters.keys())
    for name in expected_params:
        estimate = result.parameters[name]
        assert estimate.std >= 0
        assert estimate.ci_95[0] <= estimate.mean <= estimate.ci_95[1]

    assert "rhat" in result.diagnostics
    assert "effective_sample_size" in result.diagnostics
    assert result.n_cases == len(CASES)
    assert result.diagnostics["n_samples"] == 80


def test_inference_artifacts_contain_posterior_samples(tmp_path):
    network = build_default_network()
    config = InferenceConfig(
        num_warmup=50,
        num_samples=60,
        num_chains=1,
        persist_netcdf=False,
        posteriors_dir=tmp_path,
    )
    artifacts = run_inference(cases=list(CASES), network=network, config=config)
    samples = artifacts.posterior_samples
    assert "p_transmit" in samples
    assert samples["p_transmit"].shape[0] == 60
    assert samples["p_transmit"].min() >= 0.0


def test_run_inference_with_legacy_network_summary_is_accepted_for_compatibility(tmp_path):
    config = InferenceConfig(
        num_warmup=80,
        num_samples=80,
        num_chains=1,
        target_accept_prob=0.8,
        persist_netcdf=False,
        posteriors_dir=tmp_path,
        network_summary={"mean_weighted_degree": 2.5},
    )
    artifacts = run_inference(cases=list(CASES), network=None, config=config)
    assert artifacts.result.n_cases == len(CASES)
    assert "p_transmit" in artifacts.result.parameters
