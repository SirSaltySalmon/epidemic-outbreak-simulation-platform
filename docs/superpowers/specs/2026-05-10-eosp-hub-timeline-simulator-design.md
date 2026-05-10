# EOSP hub timeline simulator — design specification

**Date:** 2026-05-10  
**Status:** Approved for implementation planning  
**Replaces:** Retired vectorised SEIPHR+D ABM and Monte Carlo ensemble documented in `[docs/ABM_RETIREMENT.md](../../../ABM_RETIREMENT.md)`.  
**Related types:** `[CaseRecord](../../../app/eosp/core/models.py)` in `app/eosp/core/models.py`.

---

## 1. Purpose

Build a **forward epidemic simulator** for EOSP that:

- Avoids the retired model’s **hard cap on agent count** by using an architecture suited to **open-ended** infected and contact outcomes (exact implementation is left to the implementation plan).
- Retains **defensible hub transmission logic**: event-based contacts at airports, **load-dependent** risk when many infectors share a hub, and **state-dependent** infectiousness.
- Consumes the **same integration seams** as the retired engine: inference remains separate; `**ForecastResponse`** and cached forecasts feed the **dashboard** and `**build_outbreak_geo`** heat layer.
- Uses a **calendar timeline** anchored to **line-list symptom onsets**, with explicit rules for **observed vs synthetic** agents and for **replay** of spatial summaries.

This document is **not** statistical inference inside MCMC; it is **scenario forward simulation** (Monte Carlo or equivalent) optionally parameterised by inference outputs.

---

## 2. Non-goals

- Claiming calibrated counterfactual “truth”; the model is **scenario + narrative-constrained** (e.g. index case skip).
- Modelling **in-cabin flight** mixing separately; all post-flight processes are **lumped at the arrival airport**.
- Fitting contact counts or dispersion from sparse data in v1; **fixed** distributional families and means, with tunable globals.

---

## 3. Timeline and calendar anchor

### 3.1 Narrative exclusion

- **One designated index case** (cruise-ship narrative) is **excluded** from simulation forcing and from the **anchor** computation. Implementation must identify this row by a **stable rule** (e.g. configurable `case_id`, patient identifier, or data flag) documented in `CLAUDE.md` / job config.

### 3.2 Eligible case set

- Cases **included** in the run must have a **non-null** `location_airport_code` that appears in the **airport/route database** used for the world graph. Others are **ignored** for hub forcing (consistent with the user requirement).

### 3.3 Day 0

- **Simulation day 0** is the **minimum** `symptom_onset_date` over the **eligible** set **after** removing the index case.
- The **horizon** is **30 consecutive calendar days** starting at day 0 (inclusive): days 0 \ldots 29 as date objects.

### 3.4 Observed agents and onset (Option B)

- For **pinned observed** agents, **no** latent or infectious contribution to **hub load**, **transmission**, or **metric A** (infectious presence) **before** their `symptom_onset_date` on the simulation calendar.
- From **onset date** onward, they follow the **fixed-agent** rules in §6. This deliberately simplifies biology (no pre-onset shedding for observed rows); **synthetic** agents still use a full **E → P → I → …** progression (§5).

---

## 4. Data inputs


| Input                                                 | Use                                                                                                                                           |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `**CaseRecord`**                                      | Symptom onset, optional hospitalisation/death dates, `location_airport_code`, cohort fields for non-individual rows                           |
| `**airports.dat` / `routes.dat**` (OpenFlights-style) | Valid IATA set, **route multiplicity** for weighted sampling (more duplicate (origin, dest) rows → higher weight)                             |
| **Inference / scenario parameters**                   | e.g. `p_transmit`, scales, CFR priors—wired like the retired **ensemble** parameter draws (exact mapping in implementation plan)              |
| **Scenario catalog**                                  | `[scenarios.json](../../../app/eosp/data/scenarios.json)` pattern: overrides for contact means, NB dispersion, horizon, index exclusion, etc. |


### 4.1 Cohort rows

- `observation_kind == cohort` and related fields (`cohort_size`, `cohort_deaths`, report windows) are **in scope** for tallies and dashboard truth, but **pinned fixed-agent** semantics apply per **individual** line when `observation_kind == individual`. To address this, spawn synthetic agents based on the number on the cohort at the symptomatic stage to simplify this implementation, without the need to pin them.

---

## 5. Disease states and progression (synthetic agents)

States align conceptually with the retired ABM (**E** latent non-infectious, **P** presymptomatic with lower transmission weight, **I** symptomatic, **H** hospitalised with **frozen mobility** and **very low** transmission, **R** / **D** terminal).

- **E → P → I**: drawn durations consistent with EOSP inference / legacy priors (gamma / Weibull families as today’s codebase expects); **no transmission from E**.
- **I →**: with scenario probabilities: enter **H** vs direct **R**/**D**, matching retired intent.
- **H →** **R** or **D** with hospital CFR / timing rules consistent with scenarios.
- **R** and **D**: **no further movement** or contacts; counted in aggregates.

Transmission **weights** by state follow the qualitative ordering: **P** < **I**, **H** ≪ **I** (fixed multipliers in scenario).

---

## 6. Agent classes

### 6.1 Synthetic (autonomous) agents

- Created when a **contact** produces a successful transmission.
- **Home airport**: with probability **1/4**, home is the **current** hub; with probability **3/4**, home is sampled from **airports reachable via routes**, weighted by **route frequency** (duplicate route rows increase weight).
- **Mobility**: agents may **stay** at hub or **travel** according to scenario Markov rules; **home hub** favours longer stays / returns after visiting non-home hubs. If currently at a foreign hub (even if just spawned), behaviour is restricted so they do **not** chain arbitrary third hubs without returning home—the **user rule** (“strictly does not go to a different airport else they can't get home”) is honoured by a **simple state machine** (exact transitions in implementation plan).

### 6.2 Fixed (observed / line-list) agents

- Initialised from **eligible** `CaseRecord` rows (minus index exclusion).
- **Pinned** at `location_airport_code` from `**symptom_onset_date`** until `**hospitalization_date**` if present.
- `**hospitalization_date**`: on that date the agent transitions to **H** at **that** coded location unless the spec prefers an abstract hospital bucket—default is **same IATA** for heatmap coherence; implementation may remap if data provides a distinct facility code later.
- `**death_date`**: on that calendar date transition to **D** per timeline; no autonomous branch.
- If **death** is **not** recorded: after resolution of infectious / hospital pathway (implementation defines whether handoff triggers after **I** or after **H** discharge), agent becomes **autonomous** only for **R vs D** completion (movement already frozen per rules above).

Observed agents **may** contribute to the **per-onset-day contact burst** (§7.2).

---

## 7. Contacts, loads, and transmission

### 7.1 Event types

Whenever an infectious synthetic agent **arrives at a hub** distinct from immediate prior hub context, draw **contacts** with **mean \mu_{\mathrm{travel}} = 20** (tunable).

When they **remain** at the same hub for the day (or timestep—implementation aligns to **daily** calendar), draw contacts with **mean \mu_{\mathrm{stay}} = 5** (tunable).

### 7.2 Distributional family

- Use **Negative Binomial** with **globally fixed** overdispersion **not** inferred from data (scenario key, e.g. concentration parameter r) so variance exceeds mean (“fat tails”).

### 7.3 Load-dependent risk

- At hub h on calendar day d, aggregate **effective infectious weight** L_{h,d} over all agents contributing to mixing (§3.4 excludes pre-onset observed from L).
- Each contact drawn for a recipient adjusts infection probability **monotonically** with g(L_{h,d}) where g is **scenario-defined** (e.g. affine or saturating curve). Multiple infectors thus **elevate risk** beyond independent draws with constant p.

### 7.4 Line-list forcing burst

For each **included** case, on `**symptom_onset_date`**, simulate **contacts** drawn with mean **20** (same family as \mu_{\mathrm{travel}}) at `**location_airport_code`**; apply load rule and transmission. Cases without valid hub remain **skipped**.

---

## 8. Outputs

### 8.1 Global aggregates (per Monte Carlo trajectory and scenario)

- **Total cases**: **observed cohort included in run** plus **model-generated incident infections** aligned to tally definitions (excluding **S** susceptibles entirely); clarify in UI copy as **“simulation-relevant incidence + line list participants.”**
- **Recovered** and **dead**: sum **terminal R / D** for **both** observed-timeline paths and autonomous completions.

### 8.2 Forecast API shape

- Reuse `**ForecastResponse`** / `**ForecastPoint**` percentiles (`cases_*`, `deaths_*`) as in retirement doc; percentile bands summarise **ensemble** uncertainty.
- Extend `**metadata`** with **schema-versioned** blocks for geo and replay (§9–§10).

---

## 9. Geo heatmap and dashboard semantics

### 9.1 Keys

- Prefer **IATA airport codes** consistent with `[geo.py](../../../app/eosp/services/geo.py)` and `iata_from_destination` handling; `**ship`** sentinel remains excluded from hub heat logic unless product explicitly revives ship buckets.

### 9.2 Primary dashboard layer (non-replay)

- The **main** outbreak heatmap on the dashboard shows **metric C only**: **median cumulative infected stock** per hub aggregated across simulations (matching current consumer expectation built around `**cumulative_infected`** / final-day pattern in `_risk_heatmap_rows_from_abm_geo_forecast`).

### 9.3 Wiring

- `[build_outbreak_geo](../../../app/eosp/services/geo.py)` continues to consume a `**abm_geo_forecast`-compatible** envelope (possibly renamed later) sourced from `**get_cached_forecast("baseline").metadata`** or successor. Any rename must retain a **backward-compatible read path** until cache invalidation completes.

---

## 10. Replay artefact

For **replay mode** on the future timeline UI:

- Persist **sparse** summaries **per calendar date** d within the horizon and **per airport** k:
  - **A**: **median infectious presence** / pressure (weighted **P+I**—or engine-equivalent strictly defined once—**not counting pre-onset observed** per §3.4).
  - **C**: **median cumulative infected stock** at hub k on d (inventory rule: **ever attributed** to k per implementation accounting convention; must match dashboard C semantics when aggregated to final d_{\max}).
- **Omit pairs** `(d,k)` whose stored median is **zero** (or omit entire days with no positives—implementation chooses one consistent compaction rule documented in payload `schema_version`).
- **Provenance**: `ensemble_spec_hash`, `n_simulations`, `scenario`, inference version—as retirement metadata did.

Replay client provides **toggle** visibility for **A** vs **C**; (similar to current dashboard default heatmap allowing toggle for recorded vs. predicted) dashboard default remains **§9.2 C-only**.

Suggested JSON skeleton (informative):

```json
{
  "schema_version": "eosp_geo_replay_1",
  "anchor_date": "2026-03-01",
  "days": [
    {
      "date": "2026-03-01",
      "airports": {
        "JFK": {"A_median": 2.5, "C_median": 14.0}
      }
    }
  ]
}
```

Exact field names and whether final day echoes static heatmap duplicate are **implementation choices** guarded by schema version.

---

## 11. Integration seams


| Location                            | Responsibility                                                                                                                           |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `**app/eosp/services/forecast.py`** | Replace `**SimulatorRemovedError**` path with `run_forward_model` (name TBD); optional in-memory forecast cache matching prior behaviour |
| `**app/eosp/services/jobs.py**`     | After inference, invoke new ensemble loop across scenarios                                                                               |
| `**app/eosp/api/routes.py**`        | Keep `**POST /forecasts/run**` response contract where possible                                                                          |
| `**app/eosp/core/repository.py**`   | `**cache_forecast` / `get_cached_forecast**` persist `metadata` including geo + replay                                                   |
| `**app/eosp/services/geo.py**`      | Consume new geo block; optionally second layer payload for replay client                                                                 |
| `**docs/ABM_RETIREMENT.md**`        | Should link this spec once the engine ships (see §1).                                                                                    |


Inference may supply `**InferenceConfig.network_summary**` without building a graph, per retirement §6.2—the simulator independently uses `**routes.dat` / `airports.dat**` for mobility.

---

## 12. Testing and acceptance

- **Determinism:** fixed RNG seeds reproduce trajectories **and** median surfaces for small N smoke tests.
- **Anchor:** excluding index shifts day 0; unit test with synthetic `CaseRecord` list.
- **Option B:** on day before onset at hub k, observed agent contributes **zero** to **A** and **L**
- **Tallies:** hand-constructed microscopic scenario with known branches (one transmission, one death) verifies R/D totals.
- **Geo contract:** regression test that `**cumulative_infected` median final day** matches dashboard heatmap extractor expectations.

---

## 13. Deferred / follow-up

- Back-projection or joint calibration of \mu_{\mathrm{travel}}, load function g, and r from line list or serology **if data arrives**.
- **Per-trajectory** replay dumps for forensic debugging (heavy).
- Separate **cohort aggregation** UX for bursts vs individuals.

---

## 14. Approvals checklist

The following conversational decisions are embodied above:


| Topic              | Decision                                                                                                          |
| ------------------ | ----------------------------------------------------------------------------------------------------------------- |
| Modelling paradigm | Event-driven hub contacts (**Approach 1**)                                                                        |
| Latent infection   | Full **E** for synthetic only                                                                                     |
| Observed pre-onset | **Option B** — no shedding before `**symptom_onset_date`**                                                        |
| Contacts           | Means **20** (travel/arrival/onset burst) vs **5** (stay); **Negative Binomial** with fixed dispersion by default |
| Route sampling     | Frequency-weighted by duplicate routes                                                                            |
| Index case         | **Skipped** narrative                                                                                             |
| Horizon            | **30** days from **min eligible onset** (post-skip anchor)                                                        |
| Observed mobility  | Fixed until hospital; death time from record or autonomous completion for R/D                                     |
| Tallies            | **Observed + synthetic** aligned to timeline                                                                      |
| Heatmap default    | **C** (median cumulative infected)                                                                                |
| Replay             | Sparse daily **A_median** and **C_median** with toggles                                                           |
| Inference          | Runs **outside** forward kernel; summaries feed parameters                                                        |


**Sign-off:** Product owner confirms this spec captures intent before invoking the `**writing-plans`** skill for the implementation breakdown.