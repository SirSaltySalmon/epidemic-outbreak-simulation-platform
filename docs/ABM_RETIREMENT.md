# ABM simulator retirement (legacy reference)

This document records what was removed, how the retired **agent-based model (ABM)** and **Monte Carlo ensemble** worked technically, how that differed from **Bayesian inference**, and where a **replacement simulator** should plug into EOSP.

**Preserved:** [`app/eosp/services/inference.py`](../app/eosp/services/inference.py) (NumPyro NUTS, priors, Negative-Binomial likelihood on daily onsets, posterior persistence).

---

## 1. Deletion checklist (code removals)

The following were **removed** or **replaced with stubs** as part of this retirement:

| Item | Action |
|------|--------|
| [`app/eosp/services/abm.py`](../app/eosp/services/abm.py) | **Deleted** — vectorized SEIPHR+D kernel |
| [`app/eosp/services/ensemble.py`](../app/eosp/services/ensemble.py) | **Deleted** — `run_ensemble`, sampling, aggregation, geo forecast rollup, seed helpers |
| [`app/eosp/services/geo_buckets.py`](../app/eosp/services/geo_buckets.py) | **Deleted** — bucket labels for ABM map accounting |
| [`app/eosp/services/forecast.py`](../app/eosp/services/forecast.py) | **`build_forecast` / `run_forecast_engine`** call hub timeline Monte Carlo (`eosp.services.hub_timeline`); see [hub timeline design spec](../superpowers/specs/2026-05-10-eosp-hub-timeline-simulator-design.md) |
| [`app/eosp/services/jobs.py`](../app/eosp/services/jobs.py) | **Full refresh** runs inference **then** hub timeline Monte Carlo per scenario; caches forecasts (see [`hub_timeline`](../app/eosp/services/hub_timeline/)) |
| [`app/eosp/scripts/run_forecasts.py`](../app/eosp/scripts/run_forecasts.py) | **Inference refit CLI** — does not run forward simulation; use API/job refresh or `forecast.build_forecast` for MC |
| [`app/eosp/services/itinerary.py`](../app/eosp/services/itinerary.py) | **Updated** — state constants inlined (no `abm` import) |
| Tests | **Removed:** `test_abm.py`, `test_abm_geo_buckets.py`, `test_ensemble_geo_forecast.py`, `test_ensemble_scenario_params.py`, `test_seed_scaling.py`, `test_itinerary_abm_model.py`; **rewritten:** `test_api.py`, `test_jobs.py`, `test_geo_abm_heatmap.py`; **added:** `test_case_statistics.py` (line-list helper) |

**Kept for inference (not ABM execution):**

- [`app/eosp/services/network.py`](../app/eosp/services/network.py) — `ContactNetwork`, `build_default_network`, `degree_summary()`
- [`app/eosp/services/world_builder.py`](../app/eosp/services/world_builder.py) — hub cohort + itinerary patches used to build networks
- [`app/eosp/services/openflights_routes.py`](../app/eosp/services/openflights_routes.py), [`schedule_baseline.py`](../app/eosp/services/schedule_baseline.py), [`case_seed.py`](../app/eosp/services/case_seed.py)

**Cache / DB:** Forecast caches are repopulated by **`build_forecast`** (hub timeline). **Invalidate or version** cached rows if `ForecastResponse` / `geo_forecast` schema changes.

---

## 2. Technical retrospective — ABM kernel (`abm.py`)

### 2.1 State machine

Per-agent state was an `int8` vector of length `n_agents` with:

| Code | Meaning |
|------|---------|
| `S=0` | Susceptible |
| `E=1` | Exposed (latent; **no transmission** from E) |
| `P=5` | Presymptomatic infectious |
| `I=2` | Symptomatic infectious |
| `H=6` | Hospitalized (mobility effectively frozen; tiny `eps_hosp` transmission weight) |
| `R=3` | Recovered |
| `D=4` | Dead |

**Progression:** `E → P → I` driven by per-agent counters (`e_remaining`, `p_remaining`). **E** duration: `Gamma(2, incubation_mean/2)` total split into E vs P segments. **P** step counter reached 0 → **I** with infectious duration `Weibull(1.5) * infectious_scale`. **I** ending → with prob `p_hosp` enter **H** (duration Weibull × `hospital_scale`), else **R** or **D** with prob `cfr`. **H** ending → **R** or **D** with `cfr_H`.

### 2.2 Transmission — Track B (itinerary)

When `network.itinerary_contact_patch` was set (typical hub world):

- Each agent had a **patch index** per day (airport bucket / label index).
- **Infectious weights** `v`: presymptomatic scaled by `k_presym` (alias `presym_transmit_ratio`), symptomatic `I` weight 1, `H` scaled by `eps_hosp`.
- **Patch load:** `np.bincount(patch_ids, weights=v)` → **contact_from_patch** = load at agent patch minus self.
- Optional **flight group** mixing: same bincount on `itinerary_flight_group` when `fgroups > 0`.
- **Pressure:** `itinerary_patch_weight * contact_from_patch` or `itinerary_flight_weight * contact_from_flight` when in a flight group.
- **Exposure probability (per susceptible, per day):**

  `1 - exp(- p_transmit * h2h_multiplier * (contacts_daily / 24) * pressure)`

New infections drew new **E** durations (gamma) and **P** sub-segments.

### 2.3 Transmission — legacy graph

If no itinerary patches: **sparse adjacency** per day (`adjacency_for_day`); **contact_pressure = adjacency @ v**; same exponential form without the `/24` mix factor on patch branch.

### 2.4 Outputs (`Trajectory`)

- **`daily_counts`:** shape `(n_days+1, 7)` state histograms.
- **`cumulative_cases`:** sum of counts in E,P,I,H,R,D (everyone not S) — **stock** “ever infected” in the compartment sense, not “incident cases” only.
- **`cumulative_deaths`:** count in D.
- **`new_cases_per_day`:** transitions into E (approx incidence into latent).
- **Geo (if buckets):** `bucket_cumulative_infected[day,b]` = agents in bucket b with `state != S`; `bucket_infectious_I[day,b]` = agents in P or I in bucket b.

---

## 3. Technical retrospective — ensemble (`ensemble.py`)

### 3.1 Role

**`run_ensemble`**:

1. **`scenario.applied_to(network)`** — mutates a copy via `apply_modifications` (weights, itinerary patch multipliers from JSON).
2. **`scenario.apply_to_parameters`** — scales `p_transmit` via `p_transmit_scale`, overrides numeric params.
3. **`_draw_parameter_samples` or `_resample_posterior`** — per-trajectory draws of `p_transmit`, `contacts_daily`, `incubation_mean`, `h2h_multiplier`, `cfr` (truncated normal from inference summaries, or posterior resampling with mean alignment).
4. **`_run_trajectories`** — thread pool calling **`simulate_trajectory`** N times with distinct RNG streams.
5. **`_aggregate_points`** — for each day 1..n_days, stack `cumulative_cases` (and deaths, new cases) across trajectories → percentiles (2.5, 25, 50, 75, 97.5) → [`ForecastPoint`](../app/eosp/core/models.py) blocks.
6. **`_aggregate_geo_forecast`** — same for per-bucket arrays → `metadata["geo_forecast"]` consumed by [`geo.py`](../app/eosp/services/geo.py) for `abm_geo` heatmaps.
7. **Metadata:** `ensemble_spec_hash = sha256(n_simulations|rng_seed|scenario)[:16]`, `engine: "abm_monte_carlo"`, seed manifest, gateway weights, etc.

### 3.2 Seeding (last shipped)

**`seed_state_from_case_records`** (job path):

- Used **`line_list_compartment_targets`** in [`case_statistics.py`](../app/eosp/core/case_statistics.py): **D** from death fields; **I vs R** from `symptom_onset_date` vs forecast anchor and `still_infectious_within_days`; **E=0**; proportional shrink if totals exceeded agent pool.
- **Anchoring:** agents whose `home_iata` matched case `location_airport_code` ordered first in the seed pool.

**`seed_state_from_case_counts`** (legacy `build_forecast` path): heuristics from `inference.n_cases` only (exposed / active / recovered / deceased counts).

---

## 4. Inference vs ABM (critical distinction)

**Inference** (`run_inference`):

- Builds **`daily_onsets_from_cases`** (person-equivalents per calendar day).
- Calls **`network.degree_summary()`** → uses **`mean_weighted_degree`** inside the NumPyro model:

  `effective_contact_rate = contacts_daily + 0.4 * mean_weighted_degree`

- Likelihood: **Negative-Binomial** on expected incidence curve derived from a **closed-form growth** construction (`r_eff`, `generation_interval`), **not** by running the ABM inside MCMC.

So **posteriors** were **consistent with** a simplified renewal/growth embedding of the graph, while **forecasts** were **forward simulation** of the full ABM with uncertainty from those parameters.

---

## 5. What to reuse conceptually for v2

- **`ForecastResponse` / `ForecastPoint`** schema (percentile blocks for cases/deaths).
- **`ensemble_spec_hash`**-style provenance (tie map + curves to one stochastic run identity).
- **Scenario catalog** — parameter overrides + optional network knobs (reinterpret for new kernel).
- **Line-list seeding helpers** in `case_statistics.py` for honest initial conditions.
- **Repository `cache_forecast` / `get_cached_forecast`** contract.
- **Geo API** — `abm_geo_forecast` key name may be renamed; keep a **stable** slot in `build_outbreak_geo` for simulation-derived bucket summaries.

---

## 6. Wiring a new simulator

**Implemented:** [Hub timeline simulator design (2026-05-10)](superpowers/specs/2026-05-10-eosp-hub-timeline-simulator-design.md) — code in [`app/eosp/services/hub_timeline`](../../app/eosp/services/hub_timeline/).

```
cases → run_inference(network) → InferenceResult (+ posterior arrays)
                    ↓
         NEW: run_forward_model(scenario, inference, cases, config)
                    ↓
            ForecastResponse → repository.cache_forecast → GET /forecasts/*
                                                    → geo metadata heat layer
```

### 6.1 Integration seams (implement next)

| Location | Responsibility |
|----------|----------------|
| [`forecast.py`](../app/eosp/services/forecast.py) | `build_forecast` / `run_forecast_engine` hub timeline ensemble; optional `_FORECAST_CACHE` |
| [`jobs.py`](../app/eosp/services/jobs.py) | After `_update_inference`, runs hub timeline `build_forecast` per scenario and `cache_forecast` |
| [`routes.py`](../app/eosp/api/routes.py) | `POST /forecasts/run` unchanged contract if response shape matches |
| [`repository.py`](../app/eosp/core/repository.py) | `cache_forecast` / `get_cached_forecast` |
| [`geo.py`](../app/eosp/services/geo.py) | Accept new heat payload shape; set `risk_source` metadata |

### 6.2 Phase 2 — decouple inference from `ContactNetwork` (implemented)

**`mean_weighted_degree`** can be supplied without building a graph:

- Set **`InferenceConfig.network_summary`** to a dict containing **`mean_weighted_degree`** (other keys from `degree_summary()` are optional; only `mean_weighted_degree` is read by NUTS today).
- **`run_inference`** accepts **`network=None`** when that config field is set; otherwise it requires a **`ContactNetwork`** as before.
- **`JobManager._run_full_refresh`** skips **`build_default_network`** when the job’s inference config already carries **`network_summary`**.
- For deployments, **`EOSP_INFERENCE_MEAN_WEIGHTED_DEGREE`** (optional env var) is wired in **`inference_config_from_env()`**: when set, bootstrap/job managers use that scalar as the sole bridge into the likelihood.

To fully remove OpenFlights/world_builder from a deployment, set the env var (or inject **`network_summary`** into **`InferenceConfig`**) and ensure no other code path still imports the world builder for your workload.

---

## 7. ASCII data flow (historical)

```
CaseRecord[] ──► run_inference ──► InferenceResult
       │              │
       │              ▼
       │     degree_summary(network) ──► mean_weighted_degree ──► NUTS likelihood
       │
       └──► build_default_network ──► ContactNetwork (itinerary patches)
                      │
                      ▼
            [REMOVED] seed_state_from_case_records
                      │
                      ▼
            [REMOVED] run_ensemble ──► ForecastResponse ──► cache ──► API + geo
```

---

## 8. User-facing state

Until the **hub timeline** engine shipped, **forecast endpoints** could return **503** / **`ForecastNotCachedError`** unless **legacy cache** rows exist. Current code runs `eosp.services.hub_timeline` after inference and on-demand from Console when `[simulation]` extras are installed.
