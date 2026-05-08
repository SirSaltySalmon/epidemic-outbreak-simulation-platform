# Design: Stochastic metapopulation (Approach A) for global airport tracing

**Date:** 2026-05-08  
**Status:** Approved for planning (user selected Approach A)

## Problem

The ship-centric ABM plus fixed destination buckets cannot represent **secondary onward spread** (local transmission at a hub, then departure on arbitrary outgoing flights). The OpenSky ring heuristic in `risk_propagation.py` is **not** coupled to the same state evolution as forecasts, so “risky airports” are not emergent from a unified simulation.

## Approach (locked)

**Stochastic metapopulation on airports (and optional non-airport patches):**

- **Patches** = primary airports (IATA/metro) plus optionally one **ship** or **port** patch for the initial outbreak locus.
- **Within-patch dynamics:** discrete-time SEIR (or SEIR with exposed delay) with stochastic or mean-field local transmission.
- **Between-patch coupling:** time-varying **mobility matrix** \(M_{ij}(t)\) from **scheduled outgoing flows** (seats × load factor or normalized demand proxy). Infectious and/or exposed mass redistributes according to multinomial / Poisson departures after local mixing.
- **Outputs:** per-patch time series and **ensemble percentiles**; map layers derived from the **same** kernel (replacing decoupled ring scores over time, or running dual until parity is validated).

## Non-goals (YAGNI for v1)

- Full agent-level itineraries with gate-level micro-contacts.
- Live OpenSky as the **sole** schedule source (may **calibrate** synthetic schedules, not block on realtime).
- Re-implementing NumPyro: **reuse** existing `InferenceResult` for `p_transmit`, `incubation`-linked rates, and scenario scalars.

## Key interfaces (conceptual)

1. **`MobilitySchedule`**: for each day \(t\), sparse flows \((i \to j,\ \text{expected\_passengers})\) or dense normalized rows summing to levable outflow cap.
2. **`MetapopSimulator`**: `run(seed, schedule, params, rng)` → trajectories per patch.
3. **`GeoOutbreakAssembler`**: builds API payload: ship, case markers (unchanged from `CaseRecord`), **metapop_risk** array keyed by airport with ensemble summaries.

## Migration

- **Keep** `network_spec.json`–based ship seed and medevac legs as **initial inject** into patch state.
- **Replace** `compute_risk_zones` as the **authoritative** global layer when metapop data exists; retain old heuristic behind `?legacy_risk=1` or config flag for regression tests.
- **Inference** unchanged in v1; metapop consumes posterior **means** (and optional future: full posterior draws).

## Success criteria

- Metapop sim with a **toy 10-airport star** shows secondary hubs lighting up without being hand-listed in `network_spec` destinations.
- `/geo/outbreak` (or successor) documents `risk_source: metapop_monte_carlo` vs `legacy_opensky`.
- Unit tests cover mobility application (mass balance), one full short-horizon trajectory, and API schema stability.

## API

**GET** `/api/v1/geo/outbreak?risk_model=legacy|metapop&metapop_runs=…`

- `risk_model` — `legacy` (default) uses the existing OpenSky flight-ring heatmap; `metapop` uses the Monte Carlo metapop kernel (`metadata.risk_source`: `metapop_monte_carlo`). Any other value is treated as `legacy`.
- `metapop_runs` — optional integer (1–5000). When `risk_model=metapop`, sets the ensemble size for that request; omit to use the default from the environment.

**Environment:**

- `EOSP_METAPOP_RUNS` — default ensemble size when `metapop_runs` is not passed (default `64` in code).
- `EOSP_METAPOP_MOBILITY` — filesystem path to the mobility schedule JSON; if unset, the app uses the bundled skeleton under `app/eosp/data/`.
- `EOSP_METAPOP_SEED` — integer seed for the metapop RNG stream (default `20260507` in code).

## Open decisions (resolve in implementation plan)

- **Population model:** absolute counts per patch vs normalized shares (v1: **relative mass** with fixed ship seed mass is acceptable).
- **Who travels:** only **I** (conservative outward spread) vs **E+I** (faster); plan defaults to **E+I** with configurable split.
