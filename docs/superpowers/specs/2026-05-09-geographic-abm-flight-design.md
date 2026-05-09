# Design: Geography-first itinerary ABM with persisted near-real flights

**Date:** 2026-05-09  
**Status:** Draft — pending stakeholder review of this file  

## Relationship to prior specifications

This document captures **brainstorm-approved** decisions for **movement, persistence, coupling, and map API**. Where it conflicts with `docs/superpowers/specs/2026-05-09-entity-geographic-abm-design.md`, **this file wins** for:

- **Flight realism:** Near-real (**not** “flights never as operational truth”; see §2).
- **Ship role:** Spawn / seed context **only** — **no ship schedule**, **no ship edges** in contagion topology after policy lock.
- **`geo_bundle`:** Single assembled payload for the dashboard map; **deprecated** dual `data.geo` + `forecast_baseline.metadata.geo_forecast` wiring.

Contents of `2026-05-09-entity-geographic-abm-design.md` that remain aligned (ensemble uncertainty, deprecation of dual metapop as peer, reconciliation of layers) stay in force unless superseded explicitly below.

---

## 1. Purpose and scope

### 1.1 Product intent

Deliver a **geographically credible** outbreak narrative:

1. Seed **entities** informed by observed case data distributions (not divorced from ingestion).
2. After initial egress from the ship context, propagate infection through **timed airport-to-airport flight legs** from **near-real schedule data**.
3. Model **pre-symptomatic / incubating** transmission at distinct rates versus symptomatic phases; **hospitalized** entities **almost do not transmit** beyond a negligible leak.
4. Export **consistent totals** (cases, deaths) and **per-location summaries** coherent with movement graph nodes.

### 1.2 Non-goals (this phase)

- Seat-level cabin airflow or micro-contact tracing inside aircraft.
- **Ship routing / sailing schedule** or ship-to-non-airport hops as modeled movement.
- Joint ABM-inside-HMC/MCMC (**single runnable chain** tying every agent likelihood to globals) — defer to explicit research track.

### 1.3 Recommended implementation stance (architecture family)

Primary path: **itinerary-based ABM with simplified within-patch mixing** (versus full pairwise contacts everywhere). Complexity may increase **later** only for hotspots (optional hybrid / higher-resolution hubs).

---

## 2. Ship role, gateway allowlist, allocation (approved)

### 2.1 Ship

- Ship is **`t = 0` spawn context**: cohort size and initial latent/infectious states.
- No ship timetable; ship is **not** a traversable contagious edge beyond UI/analytics context.

### 2.2 First-airport egress allowlist (**F**, fixed gateways)

Entities may attach to the aviation graph **only** through gateways tied to regions:

| Region (product) | Aviation anchor |
|------------------|-----------------|
| South Africa — Johannesburg | Primary hub IATA (**JNB**) — authoritative list may add alternates via config |
| Qatar | Typically **DOH** |
| Switzerland | One or more IATAs (**ZRH**, **GVA**, …) fixed in config tie-map |
| Netherlands | Typically **AMS** |

**Rule:** Entities do not spontaneously appear at unrelated international airports solely from seed logic unless spawned by a separate **explicit** generator (future scope).

### 2.3 Case-informed gateway weights (**c**)

1. **Match** ingested **`CaseRecord`** rows to gateways via:
   - **Airport-first:** `location_airport_code` ∈ gateway IATA allowlist ⇒ count bucket.
   - **Country-second:** normalized `location_country` maps to gateway via config table for **CH / NL / ZA / QA** (ambiguous countries use explicit **tier order** documented in config defaults).
2. **Normalize** to non-negative categorical weights (**optional pseudocount** / Laplace smoothing TBD — default small ε to prevent lockout when counts are sparse).
3. **Per entity:** draw gateway from **categorical RNG** proportional to weights (seed-stable ensembles).
4. **Sparse ingest:** If no matches or degenerate totals → fallback to **scenario default fractions** (same hub allowlist; **sum to 1**), researcher-editable without expanding geography.

---

## 3. Flight data, itineraries, persistence, degradation

### 3.1 Normalized flight ledger (canonical internal row)

Minimal fields: **`origin_iata`**, **`destination_iata`**, **scheduled departure / arrival UTC**, **`provider_record_id`** (stable dedupe key), **`snapshot_id`** provenance.

Optional enrichment: coarse **equipment / capacity ordinal** — not mandatory v1.

### 3.2 Freshness tiers (near-real feeds)

| Tier | Meaning |
|------|---------|
| **Hot** | Latest successful pull satisfies configured max age for hubs in play |
| **Warm** | Using last-complete hub snapshot older than Hot SLA |
| **Cold** | Older daily / frozen surrogate — permissible only with explicit degraded flag surfaced to UI/metadata |

### 3.3 **Persistent snapshots (mandatory)**

Every external pull executes **write-behind**:

1. Store **immutable raw payloads** keyed by **`provider_id`**, **`query_fingerprint`**, **`fetched_at_utc`**.
2. Produce **`flight_snapshot_id`** only when normalization **completes** for the declared horizon.
3. **Simulation jobs MUST bind** **`flight_snapshot_id`** when using near-real itineraries — rerun without replaying provider pulls if freshness policy allows reuse.
4. Incremental deltas may append **successor snapshots**, never mutate prior rows silently.

**Reuse default:** Prefer latest eligible stored snapshot respecting Hot/Warm SLA; escalate only when SLA violated.

Retention: configurable TTL/archival (**raw purge** allowed separately from normalized ledger per compliance).

### 3.4 Itinerary assembly (movement after egress)

Deterministic RNG policy:

1. **Anchor \(t₀\)** for airport readiness (scenario distribution or fixed buffers).
2. **First airborne leg:** from chosen gateway airport, earliest qualifying departures respecting **minimum dwell / connection buffer**.
3. **Chaining:** bounded lookahead ( **`max_odyssey_hours`**, **max_daily_hops`** ) selects subsequent legs chronologically until horizon **H** or hospital freeze (§4).
4. **Termination:** hospitalized / removed ⇒ **enqueue no further outbound commercial legs**.
5. **Candidate tie-break:** prefer empirical traffic proxy counts if available else inverse wait-time; always **seed-stable**.

### 3.5 Co-location scaffolds

- Maintain **overlap indices** keyed by **`(flight_id_norm, dept_time_bucket)`** for optional **cabin hazards** (**§5** epidemiology attaches rates).
- **Airport terminals / landside** differentiated coarsely (start with **≤2 behavioral patch types**) to curb overfitting.

### 3.6 Failure taxonomy (explicit, never silent blending)

| Event | Reaction |
|-------|----------|
| No departing flights at hub after tier escalation | **Default (non-strict):** continue simulation with **`degraded_flight_coverage: true`** in job metadata **and** user-visible surfaced flag. **Strict mode (recommended prod research):** **abort** with remediation text (configured per environment — see §7 open point O‑1). |
| Duplicated telemetry / cancellations | Dedupe metrics; drop contradictory rows safely. |
| Unmapped/private flights | Default **exclude** unless feature toggled. |

**Strict operator mode** (recommended for publication-grade runs): **abort** if escalation hits Cold empties OR coverage minimum for **critical gateway set** unresolved.

---

## 4. Epidemiology on nodes / legs

### 4.1 Discrete states (minimal sufficient set)

Operational merge permitted for parsimony (**E** may embody presymptomatic transmission):

| State | Behaviour |
|-------|-----------|
| **S** | Susceptible |
| **E** | Latent — **transmit at presymptomatic rate \( \beta_{\text{pre}} \)** configured distinct from purely symptomatic |
| **I_s** | Symptomatic infectious — patch-level contacts + optional cabin exposures |
| **H** | Hospitalized — mobility **frozen** (**§3**); transmission leak **\( \epsilon \)** default microscopic (≤ **1e−3/day-equivalent**) |
| **R** | Recovered |
| **D** | Dead — tallied |

### 4.2 Transmission loci & rates

| Locus | Model |
|-------|-------|
| **Airport-ish patches** | Mass-action mixing or equivalent low-parameter intensity differentiated by **`patch_behavior`** tier |
| **In-flight overlap** | Poisson/Bernoulli-style contacts scaled by airborne **duration_hours** × **\( \beta_{\text{cabin}} \)** |

### 4.3 Hospitalization coupling

Admission triggers **movement freeze + leak-only transmission** baseline; optional future extension: staff pathways (explicitly deferred).

---

## 5. Statistical coupling, ensembles, forecasting alignment

### 5.1 Two-stage decomposition (explicit provenance chain)

**Stage A — Posterior inference** over global biological / reporting parameters (**progression timings, CFR family, \(\beta_{\text{pre}}, \beta_{\text{sym}}, \beta_{\text{cabin}}, \epsilon\)**…) from **WHO/cohort-aligned observations**, unchanged philosophically though parameter vector expands.

**Stage B — Conditional forward ensembles** parameterized by Stage A draws and **immutable `flight_snapshot_id`**.

Forbidden implicit behavior: KPI chart parameters drawn from disjoint legacy branch without lineage flag.

### 5.2 Uncertainty bookkeeping

Expose **`K` posterior draws × `M` stochastic seeds**. Map & headline curve share **ensemble identity** (**`ensemble_spec_hash`**).

Operational cheaper mode: **`K=1` median-parameter** nightly path — must label **narrower-than-posterior** uncertainties.

Persist per job at minimum: **`inference_version`**, **`parameter_bundle_hash`**, **`flight_snapshot_id`**, **`ensemble_spec_hash`**.

---

## 6. Unified `geo_bundle` API contract (map + provenance)

### 6.1 Single payload replaces split-brain

Client consumes **one**:

```json
{
  "observed": { "markers": [], "ingest_cursor": null },
  "simulation": {
    "flight_snapshot_id": "",
    "snapshot_as_of_utc": "",
    "freshness_tier": "hot|warm|cold",
    "inference": { "version_id": "", "parameter_bundle_hash": "", "ensemble": { "n_draws": 0, "n_seeds": 0 } },
    "layers": [
      {
        "id": "forecast_incidence_7d_med",
        "metric": "...",
        "palette": "...",
        "features": [],
        "day_range": [0, 0]
      }
    ]
  },
  "errors": []
}
```

**Sunset mandate:** eradicate parallel `forecast_baseline.metadata.geo_forecast` map plumbing in static JS (**`main.js`** path) except transitional adapter behind flag ≤ one release cycle.

### 6.2 Layer guidance (robust optics)

Prefer **moving-window incidence** / **peak infectious proxies** versus sole **terminal-day instantaneous `I` median** coloring (known blank-map failure mode historically).

Distinct optional layer: cumulative infections / attributable deaths aggregated with honest **spatial uncertainty ribbon** metadata (`metric_detail` strings).

Predicted directional flow arcs **optional**, sourced from **summed simulated leg crossings** rather than heuristic OpenSky-derived decoration when ABM narratives active.

### 6.3 Forecast KPI curve reconciliation

Displayed **baseline cases/deaths** quantiles originate from **same ensemble** powering `geo_bundle.simulation`; legacy divergent caches require **`legacy_disconnected: true`** flag if ever interim-shipped.

---

## 7. Legacy removal / deprecation (first refactor tranche)

| Item | Disposition |
|------|-------------|
| Dual-arg `renderGeoData(geo, metadataGeoForecast)` pattern | Replace with unified bundle |
| `abm_geo` heatmap fixed final-day **`infectious_I` median** only | Replace horizon-aware selectable metrics |
| **Legacy ring heat** pretending peer to ABM when scenario is ABM | Remove from default UX; optionally diagnostic sandbox |
| `metapop` **default** parallelism | Behind **`EOSP_METAPOP_ENABLED`** (already pattern) — retain only regression toggles early |
| **Ship arcs / ship legs** implying transmission along vessel route schedule | Explicitly unsupported |

Migration: server composes transitional adapter until frontend cleanup merged.

---

## 8. Verification matrix (engineering acceptance)

Automated tiers:

| Suite | Targets |
|-------|---------|
| **Flight ledger ingestion** | Idempotent upserts, DST edges, cancellations, duplication storms |
| **Snapshot isolation** | No silent mutation; rerun stability `snapshot_id=A` bitwise replay |
| **Gateway allocation (`c`)** | Synthetic fixture cases → deterministic multinomial empirical frequencies |
| **Itineraries** | Feasibility: no impossible negative layovers — property tests |
| **Epidemic smoke** | **R0-like sanity** monotone checks under parameter ramps (coarse regression) |
| **Hospital freeze** | Zero new outbound enqueue post transition except leak-only secondary attempts |
| **API contract** | JSON schema snapshots for **`geo_bundle`**, versioning field mandatory |

Golden-run artifacts: hashed **fixture flight_snapshot** pinned in-repo for deterministic CI ensembles (small **`M`**).

Manual / exploratory checklist each release candidate:

| Check | PASS criteria |
|-------|---------------|
| Cold tier surfacing | UI badge + structured log |
| Replay job | bitwise equality for aggregate counts given fixed seeds |

---

## 9. Rollout strategy

Stages:

1. **Shadow:** compute `geo_bundle` server-side unused by UI parity diff logging.
2. **Dual paint optional flag (`?geo_bundle=1`)**.
3. **Default ON** unified map; rip legacy shim after SLA window.
4. **Remove deprecated branch** deletes dead JS + backend branches only after dashboards stable.

Telemetry driving promotion: freshness tier histograms + **itineraries_partial_rate** below threshold consecutive **N** nightly builds.

---

## 10. Operator playbook (concise)

| Situation | Action |
|-----------|--------|
| API budget alarm | widen Hot SLA window consciously OR switch **Warm default** horizon (documented knob) |
| Gateway missing coverage | ingest patch OR temporary scenario fractions uplift + incident note |
| Dispute parameter bundle | rerun same **`flight_snapshot_id`** isolates inference delta |
| Forensic reproducibility complaint | cite triple: **`inference_version`**, **`flight_snapshot_id`**, **`ensemble_spec_hash`** |

---

## Open points (explicit, not blocking spec existence)

### O-1 Coverage strictness toggle

Finalize global default: **`strict_abort_missing_hub`** versus **`continue_explicit_degraded`** — spec allows both; deployment chooses per environment (**prod research** prefers strict configurable).

### O-2 Pseudocount for degenerate ingest

Pick **small ε Laplace smoothing** magnitude + max cap so one stray record cannot dominate prematurely.

---

## Document control

Authoring session: collaborative brainstorming approvals **Sections 1–5** condensed 2026-05-09. Pending user read-through gates implementation planning invocation (`writing-plans`) per project superpowers workflow.
