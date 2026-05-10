# Bayesian Inference Reimplementation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old PRD / ship-era Bayesian growth likelihood with a hub-timeline-aligned inference model whose `p_transmit` means "per casual hub contact" and stays compatible with the current forecast pipeline.

**Architecture:** Keep NumPyro NUTS as the inference engine, but stop fitting against `ContactNetwork.degree_summary()` and stop using inferred `contacts_daily` to rescale hub simulator contact means. Add a small deterministic hub-timeline surrogate likelihood that uses the same calendar anchor, eligible hub cases, contact means, state weights, load scaling, and onset burst semantics as `app/eosp/services/hub_timeline`, with nuisance terms for externally introduced / already-observed line-list cases.

**Tech Stack:** Python 3.11+, NumPy, JAX, NumPyro, ArviZ, existing EOSP `hub_timeline` modules. No new runtime dependency is required for the main implementation.

---

## Investigation Summary

The forward simulator is now hub-centric and lives in `app/eosp/services/hub_timeline/`. The legacy ship/contact graph is not part of the stochastic kernel.

Current high-risk mismatches:

- `app/eosp/services/inference.py` still advertises FR-2.1 PRD priors and samples `p_transmit ~ Beta(1.5, 20.0)`, `contacts_daily ~ Gamma(2.5, 1.0)`, `h2h_multiplier ~ LogNormal(0, 0.5)`, and `cfr ~ Beta(8, 12)`.
- `_run_nuts()` still computes `effective_contact_rate = contacts_daily + 0.4 * mean_weighted_degree`, even though `mean_weighted_degree` only describes the retired graph / itinerary approximation.
- `app/eosp/services/hub_timeline/ensemble.py` uses inferred `contacts_daily` as a scale factor for `mu_travel`, `mu_stay`, and `mu_onset_burst`, so a posterior parameter fitted in one model changes contact volume in a different model.
- `app/eosp/data/scenarios.json` describes reduced travel through `parameter_overrides.contacts_daily`; this preserves the same mismatch in scenario semantics.
- `app/eosp/api/routes.py` still has a fallback `p_transmit = 1.5 / 21.5`, mirroring the old high prior.

Smoke check from the current code on 2026-05-10:

| Run | Inputs | Result |
| --- | --- | --- |
| Current seed posterior | `p_transmit=0.089`, one baseline trajectory, 20-day direct kernel run | day 14 = 332 global cases, day 20 = 1,339 global cases |
| Current seed posterior | same, 30-day `run_hub_timeline_forecast` | timed out after 64 seconds, consistent with uncontrolled growth |
| Same kernel, low casual contact | forced `p_transmit=0.012`, `h2h_multiplier=1.0`, 30 days | day 14 = 13, day 20 = 19, day 30 = 30 |

That confirms the main issue is not the hub simulator alone. The simulator is behaving like it was given a close-contact transmission probability and asked to apply it to casual hub contact streams.

## Recommended Model Direction

Use Approach 2 as the target and ship Approach 1 first as a short safety patch.

| Approach | Description | Trade-off |
| --- | --- | --- |
| Approach 1: prior + mapping cleanup | Low `p_transmit` prior, narrow `h2h_multiplier`, realistic `cfr`, remove `mean_weighted_degree`, and stop inferred `contacts_daily` from scaling simulator contact means. | Fastest way to stop runaway forecasts, but the likelihood is still a coarse growth model. |
| Approach 2: hub mean-field surrogate | Fit against a deterministic expectation of the hub timeline kernel: observed fixed cases contribute calendar load and bursts; expected synthetic infections propagate through E/P/I/H delays; likelihood compares daily onsets using a Negative Binomial observation model. | Best fit for this project now: still uses NumPyro, no new dependencies, and shares semantics with the simulator. |
| Approach 3: simulation-based inference | Train a neural posterior estimator over full stochastic simulator summaries. | More statistically flexible, but adds new dependencies, compute cost, and deployment complexity. Keep as research follow-up. |

The v2 contract:

- `p_transmit` is per casual hub contact, not per household, cabin, ship, or close-contact exposure.
- Default `p_transmit` prior is `Beta(1, 80)` with mean about 1.2%.
- `h2h_multiplier` is a modest multiplicative uncertainty term, `LogNormal(0, 0.25)`.
- `cfr` prior is `Beta(2, 50)` unless a scenario or disease profile overrides it.
- `contacts_daily` is not inferred. Scenario contact volume belongs in `hub_timeline.contacts`.
- `mean_weighted_degree` is deprecated for inference. `InferenceConfig.network_summary` remains accepted for one release for compatibility, but v2 ignores it and records a diagnostic warning.
- Observed line-list cases are treated as calendar forcing. The likelihood includes an external/background observed-onset component so NUTS is not forced to explain every ragged observed onset as hub transmission.

## File Map

| Path | Responsibility |
| --- | --- |
| `app/eosp/core/compute_config.py` | Add explicit v2 prior and hub-likelihood config fields. |
| `app/eosp/services/inference.py` | Update priors, remove degree-driven contact rate, call hub surrogate features, preserve posterior persistence. |
| `app/eosp/services/hub_timeline/inference_surrogate.py` | New deterministic mean-field arrays used by NumPyro likelihood. |
| `app/eosp/services/hub_timeline/ensemble.py` | Stop scaling `mu_*` from inferred `contacts_daily`; draw only simulator-consumed parameters. |
| `app/eosp/data/scenarios.json` | Move reduced-travel semantics from `parameter_overrides.contacts_daily` into `hub_timeline.contacts`. |
| `app/eosp/api/routes.py` | Replace old fallback prior mean. |
| `app/eosp/core/bootstrap.py` | Stop promoting `EOSP_INFERENCE_MEAN_WEIGHTED_DEGREE` as an active v2 knob; keep it harmless for compatibility. |
| `app/eosp/services/network.py` | Mark `degree_summary()` as legacy-only for old inference mode and tests. |
| `tests/test_inference.py` | Update expected posterior parameters and no-network behavior. |
| `tests/test_inference_hub_alignment.py` | New regression tests for priors, contact scaling, and bounded low-transmission forecasts. |
| `docs/ABM_RETIREMENT.md` | Document that inference is no longer graph / ship-degree based. |

---

### Task 1: Add Explicit Inference v2 Config

**Files:**
- Modify: `app/eosp/core/compute_config.py`
- Modify: `app/eosp/core/bootstrap.py`
- Test: `tests/test_inference_hub_alignment.py`

- [ ] **Step 1: Write the failing config test**

```python
# tests/test_inference_hub_alignment.py
from eosp.core.compute_config import InferenceConfig


def test_default_inference_config_uses_low_casual_contact_prior():
    cfg = InferenceConfig()
    assert cfg.inference_model == "hub_surrogate_v2"
    assert cfg.p_transmit_prior_alpha == 1.0
    assert cfg.p_transmit_prior_beta == 80.0
    assert cfg.p_transmit_prior_alpha / (cfg.p_transmit_prior_alpha + cfg.p_transmit_prior_beta) < 0.013
    assert cfg.h2h_log_sigma == 0.25
    assert cfg.cfr_prior_alpha == 2.0
    assert cfg.cfr_prior_beta == 50.0
```

Run: `pytest tests/test_inference_hub_alignment.py::test_default_inference_config_uses_low_casual_contact_prior -v`

Expected before implementation: `AttributeError: 'InferenceConfig' object has no attribute 'inference_model'`.

- [ ] **Step 2: Add the config fields**

```python
# app/eosp/core/compute_config.py
@dataclass
class InferenceConfig:
    num_warmup: int = 1000
    num_samples: int = 2000
    num_chains: int = 4
    target_accept_prob: float = 0.85
    rng_seed: int = 20260507
    persist_netcdf: bool = True
    posteriors_dir: Path | None = None
    network_summary: dict[str, float] | None = None
    inference_model: str = "hub_surrogate_v2"
    p_transmit_prior_alpha: float = 1.0
    p_transmit_prior_beta: float = 80.0
    h2h_log_sigma: float = 0.25
    cfr_prior_alpha: float = 2.0
    cfr_prior_beta: float = 50.0
    background_onset_mean: float = 0.35
    background_onset_concentration: float = 2.0
    reporting_fraction_alpha: float = 8.0
    reporting_fraction_beta: float = 2.0
```

- [ ] **Step 3: Keep env compatibility without keeping the old behavior**

In `inference_config_from_env()`, still parse `EOSP_INFERENCE_MEAN_WEIGHTED_DEGREE` into `network_summary` so old deployments do not crash, but add a comment that v2 ignores this summary.

- [ ] **Step 4: Re-run the config test**

Run: `pytest tests/test_inference_hub_alignment.py::test_default_inference_config_uses_low_casual_contact_prior -v`

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/eosp/core/compute_config.py app/eosp/core/bootstrap.py tests/test_inference_hub_alignment.py
git commit -m "config: add hub-aligned inference priors"
```

### Task 2: Stop `contacts_daily` From Rescaling the Hub Kernel

**Files:**
- Modify: `app/eosp/services/hub_timeline/ensemble.py`
- Modify: `app/eosp/data/scenarios.json`
- Test: `tests/test_inference_hub_alignment.py`

- [ ] **Step 1: Write the failing scenario scaling test**

```python
# tests/test_inference_hub_alignment.py
from datetime import UTC, datetime

from eosp.core.models import InferenceResult, ParameterEstimate, TriggerType
from eosp.services.hub_timeline.config import default_hub_timeline_config
from eosp.services.hub_timeline.ensemble import _scenario_config
from eosp.services.scenarios import get_scenario


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
```

Run: `pytest tests/test_inference_hub_alignment.py::test_inferred_contacts_daily_does_not_rescale_hub_contact_means -v`

Expected before implementation: assertion failure because `mu_travel` becomes `100.0`.

- [ ] **Step 2: Remove inferred-contact scaling**

In `_scenario_config()`, delete the `infer_cd`, `scenario_cd`, and `scale` block. The function should merge `spec.hub_timeline`, apply `network_modifications.modify_weights.itinerary_patch` to `alpha_load`, and return the resulting config.

```python
def _scenario_config(
    spec: ScenarioSpec, base: HubTimelineSimulatorConfig, inference: InferenceResult
) -> HubTimelineSimulatorConfig:
    cfg = base.merged_with_scenario_hub_timeline(spec.hub_timeline)
    mw = spec.network_modifications.get("modify_weights") or {}
    itin = mw.get("itinerary_patch")
    if itin is not None:
        cfg = replace(cfg, alpha_load=cfg.alpha_load * float(itin))
    return cfg
```

- [ ] **Step 3: Move reduced-travel scenario semantics**

Change `reduced_travel_connectivity` in `app/eosp/data/scenarios.json`:

```json
"parameter_overrides": {},
"hub_timeline": {
  "contacts": { "mu_travel": 8.75, "mu_stay": 2.2, "mu_onset_burst": 8.75, "r_dispersion": 8.0 },
  "load": { "alpha": 0.08 },
  "mobility": { "p_stay_when_at_home": 0.82, "p_return_when_away": 0.82 }
}
```

Those contact means preserve the old `1.75 / 4.0` scale as an explicit scenario choice, not as an inferred posterior side effect.

- [ ] **Step 4: Update scenario text**

Replace text that says `contacts_daily` scales the kernel with text that says contact means are explicit in `hub_timeline.contacts`.

- [ ] **Step 5: Re-run the scaling test**

Run: `pytest tests/test_inference_hub_alignment.py::test_inferred_contacts_daily_does_not_rescale_hub_contact_means -v`

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add app/eosp/services/hub_timeline/ensemble.py app/eosp/data/scenarios.json tests/test_inference_hub_alignment.py
git commit -m "fix(hub-timeline): decouple contact means from inferred contacts_daily"
```

### Task 3: Add Hub Timeline Surrogate Feature Builder

**Files:**
- Create: `app/eosp/services/hub_timeline/inference_surrogate.py`
- Test: `tests/test_inference_hub_alignment.py`

- [ ] **Step 1: Write feature-builder tests**

```python
# tests/test_inference_hub_alignment.py
from datetime import date, timedelta

from eosp.core.seed_data import CASES
from eosp.services.hub_timeline.config import HubTimelineSimulatorConfig
from eosp.services.hub_timeline.inference_surrogate import build_hub_surrogate_features


def test_surrogate_features_include_onset_bursts_and_no_pre_onset_fixed_load():
    cases = [CASES[0]]
    cfg = HubTimelineSimulatorConfig(horizon_days=3, mu_stay=5.0, mu_onset_burst=20.0)
    days = [CASES[0].symptom_onset_date - timedelta(days=1), CASES[0].symptom_onset_date, CASES[0].symptom_onset_date + timedelta(days=1)]

    features = build_hub_surrogate_features(cases, sim_days=days, config=cfg)

    assert features.observed_onsets.tolist() == [0.0, 1.0, 0.0]
    assert features.fixed_contact_mu_by_day.tolist()[0] == 0.0
    assert features.fixed_contact_mu_by_day.tolist()[1] >= 25.0
    assert features.fixed_load_by_day.tolist()[0] == 0.0
    assert features.fixed_load_by_day.tolist()[1] == 1.0
```

Run: `pytest tests/test_inference_hub_alignment.py::test_surrogate_features_include_onset_bursts_and_no_pre_onset_fixed_load -v`

Expected before implementation: `ModuleNotFoundError`.

- [ ] **Step 2: Implement the feature dataclass**

```python
# app/eosp/services/hub_timeline/inference_surrogate.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from eosp.core.models import CaseRecord, ObservationKind
from eosp.services.hub_timeline.config import HubTimelineSimulatorConfig


@dataclass(frozen=True)
class HubSurrogateFeatures:
    observed_onsets: np.ndarray
    fixed_load_by_day: np.ndarray
    fixed_contact_mu_by_day: np.ndarray
    generated_contact_mu: float
    presymptomatic_weight: float
    hospital_weight: float
    alpha_load: float
    incubation_delay_days: int
    generated_infectious_duration_days: int


def build_hub_surrogate_features(
    cases: list[CaseRecord],
    *,
    sim_days: list[date],
    config: HubTimelineSimulatorConfig,
) -> HubSurrogateFeatures:
    day_index = {d: i for i, d in enumerate(sim_days)}
    n = len(sim_days)
    observed = np.zeros(n, dtype=float)
    fixed_load = np.zeros(n, dtype=float)
    fixed_mu = np.zeros(n, dtype=float)

    for case in cases:
        onset_i = day_index.get(case.symptom_onset_date)
        if onset_i is not None:
            observed[onset_i] += float(case.cohort_size)
            fixed_mu[onset_i] += float(config.mu_onset_burst) * float(case.cohort_size)
        if case.observation_kind != ObservationKind.INDIVIDUAL:
            continue
        for i, current_day in enumerate(sim_days):
            if current_day < case.symptom_onset_date:
                continue
            if case.death_date is not None and current_day >= case.death_date:
                continue
            if case.hospitalization_date is not None and current_day >= case.hospitalization_date:
                fixed_load[i] += float(config.eps_hosp)
                fixed_mu[i] += float(config.mu_stay) * float(config.eps_hosp)
            else:
                fixed_load[i] += 1.0
                fixed_mu[i] += float(config.mu_stay)

    generated_mu = (
        float(config.p_stay_when_at_home) * float(config.mu_stay)
        + (1.0 - float(config.p_stay_when_at_home)) * float(config.mu_travel)
    )
    delay = max(1, int(round(float(config.i_duration_mean) * float(config.p_fraction_mean))))
    infectious_days = max(1, int(round(float(config.i_duration_mean))))
    return HubSurrogateFeatures(
        observed_onsets=observed,
        fixed_load_by_day=fixed_load,
        fixed_contact_mu_by_day=fixed_mu,
        generated_contact_mu=generated_mu,
        presymptomatic_weight=float(config.k_presympt),
        hospital_weight=float(config.eps_hosp),
        alpha_load=float(config.alpha_load),
        incubation_delay_days=delay,
        generated_infectious_duration_days=infectious_days,
    )
```

- [ ] **Step 3: Re-run the feature test**

Run: `pytest tests/test_inference_hub_alignment.py::test_surrogate_features_include_onset_bursts_and_no_pre_onset_fixed_load -v`

Expected: `1 passed`.

- [ ] **Step 4: Commit**

```bash
git add app/eosp/services/hub_timeline/inference_surrogate.py tests/test_inference_hub_alignment.py
git commit -m "feat(inference): build hub surrogate likelihood features"
```

### Task 4: Replace the Graph Growth Likelihood With the Hub Surrogate

**Files:**
- Modify: `app/eosp/services/inference.py`
- Modify: `tests/test_inference.py`

- [ ] **Step 1: Update the inference tests for v2 parameters**

In `tests/test_inference.py`, replace:

```python
expected_params = {"p_transmit", "contacts_daily", "incubation", "h2h_multiplier", "cfr"}
```

with:

```python
expected_params = {"p_transmit", "incubation", "h2h_multiplier", "cfr", "reporting_fraction", "background_onset_rate"}
unexpected_params = {"contacts_daily"}
assert unexpected_params.isdisjoint(result.parameters.keys())
```

Keep the existing static `network_summary` test, but rename it to `test_run_inference_with_legacy_network_summary_is_accepted_for_compatibility`.

- [ ] **Step 2: Pass hub surrogate features into `_run_nuts()`**

In `run_inference()`, build features after counts and before `_run_nuts()`:

```python
from eosp.services.hub_timeline.config import default_hub_timeline_config
from eosp.services.hub_timeline.inference_surrogate import build_hub_surrogate_features

hub_cfg = default_hub_timeline_config()
features = build_hub_surrogate_features(sorted_cases, sim_days=[start + timedelta(days=i) for i in range(n_days)], config=hub_cfg)
posterior_samples = _run_nuts(counts=counts, features=features, config=config)
```

The import of `timedelta` must be added from `datetime`.

- [ ] **Step 3: Change `_run_nuts()` signature**

Replace:

```python
def _run_nuts(*, counts: Any, network_summary: dict[str, float], config: InferenceConfig) -> dict[str, Any]:
```

with:

```python
def _run_nuts(*, counts: Any, features: Any, config: InferenceConfig) -> dict[str, Any]:
```

Delete `mean_weighted_degree = ...`.

- [ ] **Step 4: Implement the NumPyro model**

Use this model body:

```python
fixed_load = jnp.asarray(features.fixed_load_by_day, dtype=jnp.float32)
fixed_mu = jnp.asarray(features.fixed_contact_mu_by_day, dtype=jnp.float32)
n_days = int(counts.shape[0])
delay = int(features.incubation_delay_days)
infectious_days = int(features.generated_infectious_duration_days)

def model(observed_daily: jnp.ndarray) -> None:
    p_transmit = numpyro.sample(
        "p_transmit",
        dist.Beta(float(config.p_transmit_prior_alpha), float(config.p_transmit_prior_beta)),
    )
    incubation = numpyro.sample("incubation", dist.Gamma(2.0, 0.25))
    h2h_multiplier = numpyro.sample("h2h_multiplier", dist.LogNormal(0.0, float(config.h2h_log_sigma)))
    cfr = numpyro.sample("cfr", dist.Beta(float(config.cfr_prior_alpha), float(config.cfr_prior_beta)))
    reporting_fraction = numpyro.sample(
        "reporting_fraction",
        dist.Beta(float(config.reporting_fraction_alpha), float(config.reporting_fraction_beta)),
    )
    background_onset_rate = numpyro.sample(
        "background_onset_rate",
        dist.Gamma(float(config.background_onset_concentration), float(config.background_onset_concentration / config.background_onset_mean)),
    )
    concentration = numpyro.sample("concentration", dist.Gamma(2.0, 0.5))

    generated_load = jnp.zeros(n_days, dtype=jnp.float32)
    generated_onsets = jnp.zeros(n_days, dtype=jnp.float32)

    for day_i in range(n_days):
        load_eff = fixed_load[day_i] + generated_load[day_i]
        contact_mu = fixed_mu[day_i] + generated_load[day_i] * float(features.generated_contact_mu)
        per_contact = 1.0 - jnp.exp(
            -p_transmit * h2h_multiplier * (1.0 + float(features.alpha_load) * jnp.maximum(load_eff, 0.0))
        )
        expected_infections = contact_mu * per_contact
        onset_i = day_i + delay
        if onset_i < n_days:
            generated_onsets = generated_onsets.at[onset_i].add(expected_infections)
            for active_i in range(onset_i, min(n_days, onset_i + infectious_days)):
                generated_load = generated_load.at[active_i].add(expected_infections)

    expected = background_onset_rate + reporting_fraction * generated_onsets
    expected = jnp.maximum(expected, 0.05)
    numpyro.sample(
        "obs",
        dist.NegativeBinomial2(mean=expected, concentration=concentration),
        obs=observed_daily,
    )
```

This is intentionally conservative. It treats observed cases as forcing and gives the likelihood a background component so `p_transmit` does not have to explain every observed line-list spike.

- [ ] **Step 5: Update `_summarize()` skip list**

Only skip `concentration`; keep `reporting_fraction` and `background_onset_rate` visible because they explain why p is not being inflated.

- [ ] **Step 6: Add diagnostics**

Add these entries to `InferenceResult.diagnostics`:

```python
"inference_model": config.inference_model,
"network_summary_ignored": config.network_summary is not None and config.inference_model == "hub_surrogate_v2",
```

- [ ] **Step 7: Re-run inference unit tests**

Run: `pytest tests/test_inference.py tests/test_inference_hub_alignment.py -v`

Expected: all tests pass. The NumPyro tests may take longer than pure unit tests but should remain within the existing test profile.

- [ ] **Step 8: Commit**

```bash
git add app/eosp/services/inference.py tests/test_inference.py tests/test_inference_hub_alignment.py
git commit -m "feat(inference): fit hub-aligned low-transmission surrogate"
```

### Task 5: Make Forecast Parameter Draws Match the New Posterior

**Files:**
- Modify: `app/eosp/services/hub_timeline/ensemble.py`
- Test: `tests/test_inference_hub_alignment.py`

- [ ] **Step 1: Add a draw-mapping test**

```python
# tests/test_inference_hub_alignment.py
from eosp.services.hub_timeline.ensemble import _inference_param_draws


def test_inference_draws_ignore_removed_contacts_daily_parameter():
    inf = _inference_with_contacts_daily(30.0)
    spec = get_scenario("baseline")
    cfg = default_hub_timeline_config()
    draws = _inference_param_draws(inf, spec, cfg)

    assert "contacts_daily" not in draws
    assert draws["p_transmit"][0] == 0.012
```

Run: `pytest tests/test_inference_hub_alignment.py::test_inference_draws_ignore_removed_contacts_daily_parameter -v`

Expected before implementation: test may already pass for returned draws, but it locks in the contract.

- [ ] **Step 2: Remove `contacts_daily` defaults from `_inference_param_draws()`**

Delete:

```python
if "contacts_daily" not in base_map:
    base_map["contacts_daily"] = float(cfg.baseline_contacts_daily)
```

Keep scenario `p_transmit_scale` compatibility through `ScenarioSpec.apply_to_parameters()`.

- [ ] **Step 3: Clamp posterior draw standard deviations**

Change p draw handling to cap wild old posterior tails:

```python
p_std = float(inference.parameters["p_transmit"].std) if "p_transmit" in inference.parameters else 0.005
p_std = min(max(p_std, 1e-9), 0.015)
```

This prevents old persisted posteriors with `std=0.034` from generating casual-contact probabilities that contradict the v2 prior while caches are being refreshed.

- [ ] **Step 4: Re-run draw mapping tests**

Run: `pytest tests/test_inference_hub_alignment.py::test_inference_draws_ignore_removed_contacts_daily_parameter -v`

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/eosp/services/hub_timeline/ensemble.py tests/test_inference_hub_alignment.py
git commit -m "fix(forecast): map v2 inference draws into hub timeline"
```

### Task 6: Update Fallbacks, Seed Data, and Documentation

**Files:**
- Modify: `app/eosp/api/routes.py`
- Modify: `app/eosp/core/seed_data.py`
- Modify: `docs/ABM_RETIREMENT.md`
- Modify: `docs/superpowers/specs/2026-05-10-eosp-hub-timeline-simulator-design.md`

- [ ] **Step 1: Replace API fallback**

In `_geo_simulation_context()` change:

```python
p_transmit = 1.5 / 21.5
```

to:

```python
p_transmit = 1.0 / 81.0
```

- [ ] **Step 2: Refresh bundled seed inference summaries**

In `app/eosp/core/seed_data.py`, set current illustrative `INFERENCES` to low-transmission values:

```python
"p_transmit": ParameterEstimate(mean=0.012, std=0.004, ci_95=(0.001, 0.032)),
"incubation": ParameterEstimate(mean=8.2, std=2.1, ci_95=(4.4, 13.1)),
"h2h_multiplier": ParameterEstimate(mean=1.02, std=0.18, ci_95=(0.72, 1.42)),
"cfr": ParameterEstimate(mean=0.038, std=0.025, ci_95=(0.006, 0.095)),
"reporting_fraction": ParameterEstimate(mean=0.80, std=0.12, ci_95=(0.52, 0.97)),
"background_onset_rate": ParameterEstimate(mean=0.35, std=0.25, ci_95=(0.03, 0.95)),
```

Remove `contacts_daily` from the seed inference parameter dictionaries.

- [ ] **Step 3: Update docs**

In `docs/ABM_RETIREMENT.md`, replace the note that `mean_weighted_degree` is still read by NUTS with:

```markdown
As of the hub-surrogate v2 inference plan, `ContactNetwork.degree_summary()` is legacy-only. The active Bayesian model conditions on hub-timeline calendar features and low casual-contact transmissibility priors.
```

In the hub timeline design spec, update the "Inference" row in the approvals checklist to mention the v2 surrogate rather than a separate old growth model.

- [ ] **Step 4: Commit**

```bash
git add app/eosp/api/routes.py app/eosp/core/seed_data.py docs/ABM_RETIREMENT.md docs/superpowers/specs/2026-05-10-eosp-hub-timeline-simulator-design.md
git commit -m "docs: align defaults with hub inference v2"
```

### Task 7: Add Bounded Forecast Regression

**Files:**
- Modify: `tests/test_inference_hub_alignment.py`

- [ ] **Step 1: Add a low-transmission forecast regression**

```python
# tests/test_inference_hub_alignment.py
from eosp.core.seed_data import CASES
from eosp.services.hub_timeline.ensemble import run_hub_timeline_forecast


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
```

Run: `pytest tests/test_inference_hub_alignment.py::test_low_transmission_baseline_forecast_stays_bounded -v`

Expected: pass. The thresholds are intentionally loose so the test guards explosions without overfitting stochastic noise.

- [ ] **Step 2: Add an optional slow posterior sanity test**

```python
import pytest


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
```

Run manually before release: `pytest tests/test_inference_hub_alignment.py::test_short_nuts_posterior_keeps_casual_contact_transmission_low -v -m slow`

Expected: pass with `p_transmit` posterior mean below 0.04.

- [ ] **Step 3: Commit**

```bash
git add tests/test_inference_hub_alignment.py
git commit -m "test(inference): guard low-transmission forecast scale"
```

### Task 8: Final Verification

**Files:**
- No source edits unless verification finds a regression.

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_inference_hub_alignment.py tests/test_inference.py tests/test_hub_timeline_ensemble.py tests/test_hub_timeline_kernel_smoke.py -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run API smoke tests that inspect inference output**

Run:

```bash
pytest tests/test_api.py::test_inference_versions_endpoint tests/test_api.py::test_forecast_run_endpoint -v
```

Expected: both tests pass. If test names differ, run `pytest tests/test_api.py -k "inference or forecast_run" -v` and require all selected tests to pass.

- [ ] **Step 3: Run the short manual forecast probe**

Run:

```powershell
@'
from eosp.core.seed_data import CASES, INFERENCES
from eosp.services.hub_timeline.ensemble import run_hub_timeline_forecast

fr = run_hub_timeline_forecast(
    scenario_name="baseline",
    cases=list(CASES),
    inference=INFERENCES[0],
    n_simulations=3,
    rng_seed=20260507,
    max_workers=1,
)
print(fr.forecast[13].cases_cumulative["median"])
print(fr.forecast[-1].cases_cumulative["median"])
'@ | python -
```

Expected: day-14 median is below 80 and day-30 median is below 250 for bundled seed data.

- [ ] **Step 4: Commit verification-only doc updates if any were needed**

If no edits were needed, do not create an empty commit.

---

## Release Notes for Implementers

- This plan intentionally does not add `sbi` or other simulation-based inference dependencies. The mean-field surrogate is the practical v2.
- Old posterior rows can still contain `contacts_daily`; v2 forecast mapping must ignore it.
- Existing cached forecasts should be invalidated or allowed to expire after this change because `p_transmit` semantics are changed from old PRD close-contact-ish probability to casual hub-contact probability.
- The UI may still display `p_transmit_mean`; the label should read "casual hub-contact transmission probability" where product copy exposes it.

## Self-Review

- No ship or cabin term remains in the target inference model.
- The plan keeps backward compatibility for stored old inference rows while changing active behavior.
- The plan includes tests for prior defaults, contact scaling, feature construction, draw mapping, bounded forecasts, and focused integration paths.
- The planned posterior still returns `p_transmit`, `incubation`, `h2h_multiplier`, and `cfr`, so the hub timeline ensemble can keep its existing public `InferenceResult` contract.
