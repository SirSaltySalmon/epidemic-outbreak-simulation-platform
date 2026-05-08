# Design: Entity-based geographic simulation with unified heatmaps

**Date:** 2026-05-09  
**Status:** Approved for implementation planning  
**Supersedes as product direction:** Stochastic metapopulation as a **user-visible** parallel kernel (`docs/superpowers/specs/2026-05-08-metapopulation-airport-design.md` remains historical reference).

---

## 1. Purpose and scope

### 1.1 Goals

- **Single coherent simulation story:** Individuals (or statistically equivalent cohorts) move among **locations** (ship, airports, optional aggregates), transmit infection along **contacts** and **travel**, and produce **forecasts** (cases, deaths) and **spatial outputs** (heatmap and per-location summaries) from **one** orchestrated model family.
- **Second-generation geographic transmission:** Infectious or latent travellers continue to move according to scheduled or sampled itineraries so risk propagates beyond primary evacuation hubs.
- **Statistical suitability:** Preserve Bayesian calibration of transmission parameters from observed case timelines; express predictive uncertainty via ensemble simulation; avoid presenting incompatible metrics on the same map without explicit hierarchy.

### 1.2 Non-goals (initial phases)

- Operational real-time flight tracking as truth data (OpenSky remains a **prior** or validation channel, not ground truth).
- Microscopic cabin CFD or seat-level models unless explicitly scoped later.
- Replacing WHO DON ingestion or case adjudication workflows.

### 1.3 Deprecation: metapop as default surface

The compartment **metapopulation** kernel (`run_ensemble_metapop`, patch SEIR + mobility schedule) solved “global airport connectivity vs coarse ABM buckets” but **overlaps conceptually** with an extended ABM and forces two interpretations on the map. Per product decision:

- **Default deployment:** Metapop is **disabled** for API/jobs unless `EOSP_METAPOP_ENABLED=true`.
- **Code retention:** Modules (`metapop.py`, `metapop_seed.py`, mobility loaders) stay in-repo for **regression**, **parity checks** against the new geographic ABM during development, and optional research builds.
- **User-visible behaviour:** Heatmap defaults to **legacy OpenSky ring heuristic** until geographic ABM exports replace it; documentation must not advertise dual kernels as peers.

---

## 2. Epidemiological and statistical framing

### 2.1 Layers of inference

| Layer | Role |
|--------|------|
| **Observation model** | Cases/deaths (and cohort structure) tied to locations and dates; feeds likelihood. |
| **Process model** | SEIR+D (or variant) on a **contact graph** / **location graph** with travel; drives states forward. |
| **Parameter inference** | Bayesian posterior over global/national parameters (e.g. `p_transmit`, duration, CFR) from NumPyro or equivalent — **unchanged in role**. |
| **Projection / risk** | Monte Carlo ensembles sample from posterior (or posterior predictive); summaries are **percentiles** on counts or derived risks. |

### 2.2 Suitability for the outbreak narrative

- **Ship-centric seed + international spread** fits a **network** formulation with explicit nodes (ports/airports) and timed edges (legs). Aggregate patch models average away ship-level stochasticity; pure ring heuristics lack mechanistic travel. The **entity-extended ABM** (or hybrid) restores a single process model aligned with the narrative.
- **Uncertainty:** Keep **ensemble percentiles** on outputs (cases, deaths, location-scale prevalence proxies). Report **assumption sensitivity** (mobility bundle version, schedule sparsity) in metadata.
- **Identifiability:** Global parameters remain informed by reported totals; **fine geographic risk** will be **partially informed** by mobility priors and ship/evacuation topology unless airport-level incidence is observed — the UI must label geographic layers as **model-structured risk**, not confirmed incidence, unless validated against subnational data.

### 2.3 Relationship to legacy heatmap

Until the geographic ABM exports land-wide risk, **legacy** `compute_risk_zones` remains the **default** orange layer: a **connectivity prior**, not a duplicate forecast. The green ABM bucket overlay remains **median cumulative infected by destination cluster** from the ship-network ensemble. Future work replaces both with **one** heat source derived from the extended model.

---

## 3. Target architecture

### 3.1 Core concepts

- **Place graph:** Nodes = `SHIP`, `AIRPORT:IATA`, optional `REGION` buckets; edges = scheduled or sampled **legs** with capacity and timing.
- **Agents:** Each holds discrete infection state, timers, and **location** (node id or **in-transit on edge**). Population scale may use **representative agents** with weights if needed for performance.
- **Dynamics:** Within-node contacts (dense block or degree-controlled); **aboard-edge** contacts for flight legs (parameterised intensity). Same posterior-driven transmission parameters; scenario hooks adjust rates or network disruptions.
- **Outputs:**
  - **Global:** Same `ForecastResponse` shape (cases/deaths percentiles by day).
  - **Geo:** Single contract for heatmap — e.g. **expected infectious present at node on day t** or **cumulative infections attributed to node** — chosen once and documented in API metadata (`risk_metric_id`, `risk_metric_detail`).

### 3.2 Hybrid internal representation (optional optimisation)

If full agent resolution on all global airports is too slow, use **hybrid**: high-resolution agents on ship + active corridors; **flux coupling** between hub pairs for distant regions, still orchestrated by one simulator process and one export pipeline. The **user-facing** story remains “entities and locations,” not two kernels.

### 3.3 Migration from current ABM buckets

- Today: `geo_bucket_spec` = `ship` + destination codes from `network_spec.json`.
- Extend: bucket or tally by **airport node** and **day** as agents arrive; retain backward-compatible aggregate exports during transition.

---

## 4. Components and interfaces

| Component | Responsibility |
|-----------|----------------|
| **Network builder** | Builds place graph + agent placement from `network_spec`, mobility bundles, and inference-driven seeds. |
| **Simulator core** | Extends current vectorised ABM or adds event-driven layer for legs; ensures trajectory exposes **per-node time series** for mapping. |
| **Ensemble orchestrator** | Reuses pattern from `ensemble.py`: posterior draws → many trajectories → percentiles; adds optional geo aggregation module. |
| **Geo assembler** | Single builder for `/geo/outbreak`: replaces parallel metapop/legacy choice with **configured default** + legacy fallback flag. |
| **Frontend map** | One heat layer semantics + ABM overlay until merged; legend text driven from metadata. |

---

## 5. Deprecation mechanics (implemented baseline)

- **Setting:** `EOSP_METAPOP_ENABLED` (default `false`). When false, `risk_model=metapop` requests are coerced to **legacy**; job manager skips `metapop_geo` stage unless enabled.
- **Environment template:** `.env.example` documents legacy as default heat kernel; metapop vars marked deprecated for product surfaces.
- **Tests:** Metapop-specific API tests enable the flag explicitly.

---

## 6. Phased delivery

| Phase | Deliverable |
|-------|-------------|
| **P0** | Metapop off by default; docs + tests (this baseline). |
| **P1** | Place graph schema + loaders; agents with `location_node_id`; ship + medevac legs only; heatmap from ABM node tallies (limited airports). |
| **P2** | Scheduled mobility-driven travel for infectious/exposed; second-hop geographic risk; ensemble geo percentiles. |
| **P3** | Performance: representative agents / flux approximation; profiling budgets. |
| **P4** | Remove or archive metapop code paths once parity tests pass; legacy rings optional “debug overlay” only. |

---

## 7. Validation and testing

- **Unit:** Graph integrity, mass conservation, non-negative compartments, seed reproducibility.
- **Regression:** Snapshot exports for fixed seeds vs current ABM totals where topology overlaps.
- **Statistical:** Posterior predictive checks on held-out days (when data permits); sensitivity to mobility bundle version documented.
- **Integration:** `/geo/outbreak` + forecast cache after job run; map legend matches metadata.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Overfitting geographic detail without local data | Conservative labels; ensemble bands; mobility provenance in metadata. |
| Runtime explosion | Phased hybrid; cap airports in P1; measure before global graph. |
| User confusion during transition | Single primary heat metric; deprecate dual kernels in UI copy. |

---

## 9. Approval and next step

This document is the **design source of truth** for **writing-plans**: produce an implementation plan with concrete file-level tasks starting at P0/P1.

**Self-review:** No unresolved “TBD” placeholders for scope; metapop deprecation path explicit; statistical roles separated (inference vs projection); single heatmap contract called out for later ABM export.
