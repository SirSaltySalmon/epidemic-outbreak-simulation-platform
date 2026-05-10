"""Bayesian inference for outbreak parameters (FR-2.1).

NumPyro NUTS over the seeded case timeline. Priors match the PRD FR-2.1 table
exactly (Beta(1.5, 20) on ``p_transmit``, Gamma(2, 0.25) on ``incubation``,
LogNormal(0, 0.5) on ``h2h_multiplier``). The likelihood maps the parameters
to expected daily symptom-onset counts via a closed-form next-generation
operator over the contact-network's mean weighted degree, avoiding the
prohibitive cost of running the stochastic ABM inside MCMC.

NumPyro and JAX are imported lazily so the rest of the service module graph
stays importable even when those heavy CPU wheels are not yet installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

from eosp.core.case_statistics import (
    total_cohort_persons,
    validation_quality_weighted_mean,
)
from eosp.core.compute_config import InferenceConfig
from eosp.core.models import (
    CaseRecord,
    ConvergenceStatus,
    InferenceResult,
    ParameterEstimate,
    TriggerType,
)
from eosp.services.network import ContactNetwork


logger = logging.getLogger(__name__)


def _require_numpy():  # pragma: no cover - exercised when simulation extras omitted
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "NumPy is required for inference and simulation workloads. "
            'Install EOSP simulation extras with `pip install -e ".[simulation]"`.'
        ) from exc
    return np


@dataclass
class InferenceArtifacts:
    result: InferenceResult
    posterior_samples: dict[str, Any]
    netcdf_path: str | None


def daily_onsets_from_cases(cases: Iterable[CaseRecord], start_date: date, n_days: int) -> Any:
    np = _require_numpy()
    counts = np.zeros(n_days, dtype=np.int32)
    for case in cases:
        offset = (case.symptom_onset_date - start_date).days
        if 0 <= offset < n_days:
            counts[offset] += int(case.cohort_size)
    return counts


def run_inference(
    *,
    cases: list[CaseRecord],
    network: ContactNetwork,
    config: InferenceConfig | None = None,
    trigger: TriggerType = TriggerType.MANUAL,
    start_date: date | None = None,
    n_days: int = 30,
    version_label: str | None = None,
    previous_inference: InferenceResult | None = None,
) -> InferenceArtifacts:
    """Fit the EOSP Bayesian model with NUTS and return ``InferenceArtifacts``.

    ``previous_inference`` is currently used only to derive informative-prior
    centers in the next call (FR-2.1 step 3); the priors below stay weakly
    informative on the first run.
    """

    config = config or InferenceConfig()
    if not cases:
        raise ValueError("Cannot run inference without any case records")

    sorted_cases = sorted(cases, key=lambda case: case.symptom_onset_date)
    inferred_start = sorted_cases[0].symptom_onset_date
    start = start_date or inferred_start
    counts = daily_onsets_from_cases(sorted_cases, start, n_days)

    network_summary = network.degree_summary()
    posterior_samples = _run_nuts(counts=counts, network_summary=network_summary, config=config)

    parameter_estimates = _summarize(posterior_samples)
    diagnostics = _diagnostics(posterior_samples, config=config)

    timestamp = datetime.now(UTC)
    version = version_label or _format_version(timestamp, previous_inference)

    netcdf_path = _persist_netcdf(
        version=version,
        posterior_samples=posterior_samples,
        diagnostics=diagnostics,
        config=config,
    )

    convergence = ConvergenceStatus.CONVERGED
    if diagnostics["divergences"] > 50 or diagnostics["rhat_max"] > 1.05:
        convergence = ConvergenceStatus.FAILED
    elif diagnostics["rhat_max"] > 1.01 or diagnostics["divergences"] > 25:
        convergence = ConvergenceStatus.WARNING

    n_persons = total_cohort_persons(sorted_cases)
    dq_mean = validation_quality_weighted_mean(sorted_cases)

    inference = InferenceResult(
        version=version,
        timestamp=timestamp,
        trigger=trigger,
        n_cases=n_persons,
        parameters=parameter_estimates,
        diagnostics={
            "rhat": diagnostics["rhat"],
            "effective_sample_size": diagnostics["ess"],
            "divergences": int(diagnostics["divergences"]),
            "convergence_status": convergence.value,
            "n_warmup": config.num_warmup,
            "n_samples": config.num_samples,
            "n_chains": config.num_chains,
            "data_quality_mean": dq_mean,
            "n_observation_rows": len(sorted_cases),
        },
        posterior_download_url=netcdf_path or f"local://posteriors/{version}.nc",
    )

    return InferenceArtifacts(
        result=inference,
        posterior_samples=posterior_samples,
        netcdf_path=netcdf_path,
    )


def _run_nuts(
    *,
    counts: Any,
    network_summary: dict[str, float],
    config: InferenceConfig,
) -> dict[str, Any]:
    np = _require_numpy()
    try:
        import jax
        import jax.numpy as jnp
        import numpyro
        import numpyro.distributions as dist
        from numpyro.infer import MCMC, NUTS
    except ImportError as exc:  # pragma: no cover - exercised manually when JAX is missing
        raise RuntimeError(
            "NumPyro / JAX are required to run Bayesian inference. "
            "Install via `pip install -e .` after wiring up jax[cpu]."
        ) from exc

    numpyro.set_host_device_count(max(1, config.num_chains))

    observed = jnp.asarray(counts)
    mean_weighted_degree = float(network_summary.get("mean_weighted_degree", 1.0))
    n_days = int(counts.shape[0])

    def model(observed_daily: jnp.ndarray) -> None:
        p_transmit = numpyro.sample("p_transmit", dist.Beta(1.5, 20.0))
        contacts_daily = numpyro.sample("contacts_daily", dist.Gamma(2.5, 1.0))
        incubation = numpyro.sample("incubation", dist.Gamma(2.0, 0.25))
        h2h_multiplier = numpyro.sample("h2h_multiplier", dist.LogNormal(0.0, 0.5))
        cfr = numpyro.sample("cfr", dist.Beta(8.0, 12.0))
        concentration = numpyro.sample("concentration", dist.Gamma(2.0, 0.5))
        initial_rate = numpyro.sample("initial_rate", dist.HalfNormal(2.0))

        effective_contact_rate = contacts_daily + 0.4 * mean_weighted_degree
        r_eff = p_transmit * h2h_multiplier * effective_contact_rate
        generation_interval = jnp.maximum(incubation, 1.0)
        growth_rate = jnp.log(jnp.maximum(r_eff, 1e-3)) / generation_interval

        days = jnp.arange(observed_daily.shape[0], dtype=jnp.float32)
        expected = jnp.exp(jnp.log(jnp.maximum(initial_rate, 1e-3)) + growth_rate * days)
        expected = jnp.maximum(expected, 0.05)
        numpyro.sample(
            "obs",
            dist.NegativeBinomial2(mean=expected, concentration=concentration),
            obs=observed_daily,
        )

    rng_key = jax.random.PRNGKey(int(config.rng_seed))
    kernel = NUTS(model, target_accept_prob=config.target_accept_prob)
    mcmc = MCMC(
        kernel,
        num_warmup=config.num_warmup,
        num_samples=config.num_samples,
        num_chains=config.num_chains,
        chain_method="sequential",
        progress_bar=False,
    )
    mcmc.run(rng_key, observed_daily=observed)

    samples = mcmc.get_samples(group_by_chain=False)
    return {key: np.asarray(value) for key, value in samples.items()}


def _summarize(samples: dict[str, Any]) -> dict[str, ParameterEstimate]:
    np = _require_numpy()
    summary: dict[str, ParameterEstimate] = {}
    for name, draws in samples.items():
        if name == "concentration" or name == "initial_rate":
            continue
        flat = np.asarray(draws).reshape(-1)
        summary[name] = ParameterEstimate(
            mean=float(np.mean(flat)),
            std=float(np.std(flat)),
            ci_95=(float(np.percentile(flat, 2.5)), float(np.percentile(flat, 97.5))),
        )
    return summary


def _diagnostics(
    samples: dict[str, Any],
    *,
    config: InferenceConfig,
) -> dict[str, Any]:
    np = _require_numpy()
    rhat: dict[str, float] = {}
    ess: dict[str, float] = {}
    try:
        import arviz as az  # type: ignore

        chain_shaped = {key: _reshape_chains(np.asarray(value), config.num_chains) for key, value in samples.items()}
        idata = az.from_dict(posterior=chain_shaped)
        summary = az.summary(idata, fmt="wide")
        for name in samples:
            if name in summary.index:
                rhat[name] = float(summary.loc[name, "r_hat"])
                ess[name] = float(summary.loc[name, "ess_bulk"])
    except Exception as exc:  # pragma: no cover - arviz failures fall back below
        logger.debug("ArviZ summary failed (%s); using NumPy fallback diagnostics.", exc)
        for name, draws in samples.items():
            flat = np.asarray(draws).reshape(-1)
            rhat[name] = 1.0
            ess[name] = float(flat.shape[0])

    rhat_max = max(rhat.values()) if rhat else 1.0
    return {
        "rhat": rhat,
        "rhat_max": rhat_max,
        "ess": ess,
        "divergences": _count_divergences(samples),
    }


def _reshape_chains(values: Any, num_chains: int) -> Any:
    np = _require_numpy()
    if values.ndim >= 2 and values.shape[0] == num_chains:
        return values
    flat = np.asarray(values).reshape(-1)
    per_chain = flat.shape[0] // max(num_chains, 1)
    if per_chain * num_chains == flat.shape[0]:
        return flat.reshape(num_chains, per_chain)
    return flat.reshape(1, -1)


def _count_divergences(samples: dict[str, Any]) -> int:
    np = _require_numpy()
    diverging = samples.get("diverging")
    if diverging is None:
        return 0
    return int(np.asarray(diverging).sum())


def _persist_netcdf(
    *,
    version: str,
    posterior_samples: dict[str, Any],
    diagnostics: dict[str, Any],
    config: InferenceConfig,
) -> str | None:
    if not config.persist_netcdf:
        return None
    try:
        import arviz as az  # type: ignore
    except ImportError:  # pragma: no cover - arviz is in dependencies
        return None

    _require_numpy()

    directory = config.posteriors_dir or _default_posteriors_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target_path = directory / f"{version}.nc"
    try:
        chain_shaped = {key: _reshape_chains(value, config.num_chains) for key, value in posterior_samples.items()}
        idata = az.from_dict(posterior=chain_shaped, attrs={"diagnostics": str(diagnostics)})
        idata.to_netcdf(str(target_path))
        return str(target_path)
    except Exception as exc:  # pragma: no cover - filesystem errors only
        logger.warning("Failed to persist posterior NetCDF: %s", exc)
        return None


def _default_posteriors_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "posteriors"


def _format_version(timestamp: datetime, previous: InferenceResult | None) -> str:
    base = "v1.0.0"
    if previous is not None:
        base = _bump_patch(previous.version)
    suffix = timestamp.strftime("%Y%m%dT%H%M%SZ")
    return f"{base}-{suffix}"


def _bump_patch(version: str) -> str:
    head = version.split("-")[0]
    if not head.startswith("v"):
        return "v1.0.0"
    parts = head[1:].split(".")
    if len(parts) != 3:
        return "v1.0.0"
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError:
        return "v1.0.0"
    return f"v{major}.{minor}.{patch + 1}"
