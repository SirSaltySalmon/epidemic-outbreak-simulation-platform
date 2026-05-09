# Design: Itinerary ABM — corrected model specification

**Date:** 2026-05-09
**Status:** Draft — replaces epidemiology and inference sections of `2026-05-09-geographic-abm-flight-design.md`
**Scope:** Track B — what must be decided and specified before implementing the itinerary ABM, near-real flight data, and expanded state machine.

---

## 1. Why this document exists

The `2026-05-09-geographic-abm-flight-design.md` spec describes what we want. This document describes what must be correct before we build it. Four problems in the original make it unsafe to implement directly:

1. **The E state is mis-specified.** The spec says E is latent but transmits at `β_pre`. Latent and presymptomatic are different biological states. Getting this wrong produces a model that cannot be compared to the literature.
2. **The inference is over-parameterised relative to what the data can distinguish.** Adding `β_pre`, `β_sym`, `β_cabin`, and `ε` as free parameters to a likelihood over daily symptom onset counts will not converge to a useful posterior without hard constraints. The spec does not state which parameters are inferred and which are fixed.
3. **Case-informed gateway weights are circular.** Observed case locations describe where diagnoses were recorded, not which airport each entity first departed through. Using them as movement weights risks concentrating all simulated egress at the most-reported location and producing a misleading geographic signal.
4. **Airport transmission has no population denominator.** Mass-action mixing at an airport requires a susceptible population count. The spec does not define this pool. Without it, in-airport transmission in the model is either zero (no susceptibles) or uncalibrated (arbitrary population assumed).

Each problem is solvable. This document states the solution before code is written.

---

## 2. State machine

### 2.1 The problem with E as specified

The current ABM has `S → E → I → {R, D}`, where `E` is a latent period with no transmission. The flight spec adds presymptomatic transmission rate `β_pre` to `E`, making it simultaneously latent (not yet detectable) and infectious. These are different states.

If someone is in the true latent period, they are not shedding pathogen and cannot infect contacts. If they are presymptomatic, they are shedding but have not yet developed symptoms. The duration and infectivity differ.

### 2.2 Required states

```
S → E → P → I_s → {R, D}
                 ↘ H → {R, D}
```

| State | Meaning | Transmits | Moves | Notes |
|-------|---------|-----------|-------|-------|
| S | Susceptible | No | Yes | |
| E | Latent (truly non-infectious) | No | Yes | Duration: incubation period minus presymptomatic period |
| P | Presymptomatic infectious | At rate β_pre | Yes | May be short (1–3 days) |
| I_s | Symptomatic infectious | At rate β_sym | Yes if pre-hospitalization | Longest infectious window |
| H | Hospitalized | At rate ε ≤ 1e-3/day | No | Mobility frozen; no commercial legs enqueued |
| R | Recovered | No | Yes | |
| D | Dead | No | No | |

This adds one state (P) to the existing code. The existing `e_remaining` timer covers the full E+P period in the current code; it must be split into two timers.

### 2.3 Implementation path

The existing `state` array uses `uint8` codes. Add `STATE_P = 5`. The `simulate_trajectory` inner loop currently checks `state == STATE_E` for countdown and `state == STATE_I` for transmission; both checks expand to also handle `STATE_P`. The vectorised contact pressure calculation already handles any infectious state if you build the mask correctly:

```python
infectious_mask = (state == STATE_P) | (state == STATE_I)
presym_mask = (state == STATE_P)
sym_mask = (state == STATE_I)
# Contact pressure is β_pre × P_count + β_sym × I_count per neighbour
```

If `β_pre = k × β_sym` (a fixed ratio, not a free parameter — see §3), the contact pressure simplifies to `β_sym × (k × P_count + I_count)`, which is a single scalar multiplication over a combined vector. This keeps the matrix-vector structure unchanged.

### 2.4 H state transition

In the current code `I → {R, D}` with probability `cfr` for D. The H path branches before outcome:

```
I_s → H with probability p_hosp (new parameter, fixed from outbreak data)
I_s → {R, D} with probability (1 - p_hosp)
H → {R, D} with longer mean time and lower cfr_H
```

For the initial implementation, `p_hosp = 0` is an acceptable default that reproduces current behaviour exactly. Add the branch without changing the default until hospitalisation data is available to estimate `p_hosp`.

---

## 3. Inference: which parameters are identified

### 3.1 What daily onset counts can tell you

The inference likelihood operates over daily symptom onset times (when did cases first show symptoms). From this time series you can estimate:

- The **effective reproduction number in the early growth phase**, which is a composite: `R_eff ≈ β_sym × contacts × (duration_I + presym_fraction × duration_P)`. You cannot separate β_sym, β_pre, and contact rate from the aggregate growth rate alone.
- The **incubation period distribution** (from the spread of onset dates relative to a known exposure event).
- The **CFR** (from deaths vs cases with lag adjustment).
- Optionally, `h2h_multiplier` if you have close-contact household data that distinguishes household R from population R.

### 3.2 What is not identified

`β_pre` and `β_cabin` are not separately identified from daily onset counts. Both affect who gets infected but produce indistinguishable signatures in the aggregate. Adding them as free parameters does not add information — it adds collinearity.

**Conclusion:** Fix `β_pre = k × β_sym` where `k` is a literature-derived constant (0.5 is the midpoint of COVID-19 estimates; use `k = 0.5` as a default with a sensitivity analysis at `k = 0.2` and `k = 0.8`). Fix `β_cabin` as a separate scenario parameter informed by published cabin attack rate studies, not inferred from this outbreak's onset curve. Fix `ε` at `1e-3/day` unless hospital contact data becomes available.

The inference parameter vector remains `{p_transmit, contacts_daily, incubation_mean, h2h_multiplier, cfr}` as in the current code. `p_transmit` maps to `β_sym` in the extended model. `β_pre` and `β_cabin` are scenario knobs.

### 3.3 Sensitivity analysis (not inference)

Run three forward-ensemble scenarios varying `β_pre` ratio and `β_cabin`:

| Scenario | `k` (β_pre/β_sym) | `β_cabin` |
|----------|-------------------|-----------|
| Low presym spread | 0.2 | 0.02 |
| Baseline | 0.5 | 0.05 |
| High presym spread | 0.8 | 0.1 |

Report the headline curve spread across scenarios as a model uncertainty band, distinct from the ensemble uncertainty band from posterior sampling. Label both separately in the UI.

---

## 4. Gateway allocation

### 4.1 The circularity problem

The original spec proposes weighting each entity's first departure airport by observed case geography: if more cases are recorded from ZA than CH, more entities depart via JNB.

This is circular because:
- Case records show where diagnoses were recorded, not which airport an entity first flew from.
- An entity diagnosed in ZA may have been a ZA national who flew to ZA, or a CH national who had a layover in ZA, or a staff member who was repatriated to ZA from a different port.
- With sparse records (6 cases), one location dominates and the simulated egress concentrates artificially.

### 4.2 Correct approach: known evacuation assignments as the prior

`network_spec.json` already contains flight records with `n_passengers` per leg. These are the evacuation assignments — the actual counts of who went where. Use these as the movement prior:

```python
def gateway_weights_from_network_spec(spec: dict) -> dict[str, float]:
    counts: dict[str, int] = {}
    for flight in spec.get("flights", []):
        iata = iata_from_destination(flight["destination"])
        counts[iata] = counts.get(iata, 0) + flight.get("n_passengers", 0)
    total = sum(counts.values()) or 1
    return {iata: count / total for iata, count in counts.items()}
```

Each entity's gateway is drawn from this categorical distribution. This is grounded in the actual movement records, not diagnostic geography.

### 4.3 When to use case geography

Case records inform the **likelihood** (do the simulated infection counts match where cases were reported?) and can shift the posterior on infection risk by destination. They are not inputs to the movement model. This separation is the correct Bayesian structure.

### 4.4 Sparse records and Laplace smoothing

Add a small pseudocount (ε = 0.5 per gateway in the allowlist) before normalisation. This prevents complete lockout of a gateway that has network_spec capacity but zero confirmed cases. The pseudocount is visible in the metadata as `gateway_pseudocount: 0.5`.

---

## 5. Airport transmission — population denominator

### 5.1 The missing piece

Airport mass-action mixing requires `S_airport` — the number of susceptible people in the airport who could be infected by a transiting agent. The spec omits this. Without it, you have two choices, both bad:

- Set `S_airport = 0`: no transmission at airports. The itinerary feature does nothing epidemiologically.
- Set `S_airport = arbitrary constant`: transmission is uncalibrated and uninterpretable.

### 5.2 Recommended approach for P1: airports as relay points only

In P1, agents move between airport nodes on timed itineraries but **do not transmit to a local susceptible population at airports**. They can infect other ship-cohort agents who share the same flight (cabin transmission) but not the general airport population.

This is epidemiologically honest: the ship cohort is small (~150 agents), local airport transmission from a few transiting individuals is a second-order effect, and we have no data to calibrate `S_airport` for each hub.

Document this explicitly in the API metadata:

```json
"risk_metric_detail": "Infections among ship-cohort agents and in-flight contacts only. Airport community transmission not modelled in this version."
```

### 5.3 When to add airport community transmission (P2+)

This becomes meaningful when:
- You have patch population estimates for each airport hub (available from census/airport statistics).
- You can estimate a plausible coupling parameter (fraction of airport population potentially exposed per transiting infectious agent per hour).
- You have a validation signal (did community cases emerge near the hub airports?).

Until then, adding airport community transmission produces numbers that look precise but are not grounded in anything measurable.

---

## 6. Flight data

### 6.1 Operational decision required before implementation

The `2026-05-09-geographic-abm-flight-design.md` spec requires near-real flight data with Hot/Warm/Cold freshness tiers and immutable snapshots. This requires decisions that are not engineering tasks:

- **Which provider?** FlightAware, AviationStack, OpenSky, OAG, and Cirium have different coverage, latency, pricing, and redistribution terms.
- **License terms.** Most commercial providers prohibit storing or redistributing flight data beyond internal use. Publishing simulations that cite a specific `flight_snapshot_id` may require the provider's explicit approval.
- **Retention policy.** Raw flight payloads are potentially GDPR-relevant (tail numbers + departure times + route can identify specific aircraft operations). Decide TTL and archival before storing.
- **Cost.** A nightly baseline job running on ~5–10 gateway airports with hourly schedule polling costs 10–50 API calls per job. Price this against the provider's per-call rate before committing.
- **Degraded-mode policy.** When no fresh data is available, does the simulation run with static data, block, or surface an explicit uncertainty flag? This is a product decision, not an engineering default.

### 6.2 Interim path: static community datasets

Use [OpenFlights](https://openflights.org/data.html) route and airport data as the baseline. It is CC-BY licensed, freely redistributable, and covers all major hubs. Supplement with manually curated schedules for the specific evacuation corridors (JNB–ZRH, JNB–AMS, HLE-related) derived from published IATA schedules.

Bundle the static dataset as `data/flight_schedules_baseline.json` with a `bundle_date` field. The `flight_snapshot_id` is the SHA-256 of the file content. This is reproducible, testable, and costs nothing to operate.

When the provider decision is made, the snapshot ledger schema (`origin_iata`, `destination_iata`, `scheduled_departure_utc`, `provider_record_id`, `snapshot_id`) is the same regardless of source.

### 6.3 Snapshot ledger schema (for both static and near-real)

```sql
CREATE TABLE flight_snapshots (
    snapshot_id TEXT PRIMARY KEY,      -- SHA-256 of normalised payload
    provider_id TEXT NOT NULL,          -- "openflights_static" | "flightaware_v1" | etc.
    query_fingerprint TEXT NOT NULL,    -- hashed query parameters (hub_iata + date_range)
    fetched_at_utc TIMESTAMP NOT NULL,
    horizon_start_date DATE NOT NULL,
    horizon_end_date DATE NOT NULL,
    status TEXT NOT NULL                -- "complete" | "partial" | "degraded"
);

CREATE TABLE flight_legs (
    id INTEGER PRIMARY KEY,
    snapshot_id TEXT REFERENCES flight_snapshots(snapshot_id),
    origin_iata TEXT NOT NULL,
    destination_iata TEXT NOT NULL,
    scheduled_departure_utc TIMESTAMP NOT NULL,
    scheduled_arrival_utc TIMESTAMP NOT NULL,
    provider_record_id TEXT,           -- deduplification key
    equipment_code TEXT,               -- optional
    capacity_ordinal INTEGER           -- 1=small, 2=medium, 3=large — not exact seat count
);
```

No flight leg row is ever mutated after insert. Cancellations and amendments create new rows with `status = "cancelled"` or `status = "amended"` and a `supersedes_id` reference.

---

## 7. Itinerary assembly

### 7.1 What P1 needs

For P1 (ship-cohort only, relay-point airports), each entity's itinerary is:

1. **t = 0:** Entity is at SHIP node with assigned infection state.
2. **t = depart_day:** Entity moves to departure gateway airport based on `network_spec.json` evacuation flight assignment. This is deterministic from the spec, not sampled.
3. **t = depart_day + transit_days:** Entity arrives at destination country (optional second hop if the entity's evacuation flight has a layover; currently none in `network_spec.json`).

No near-real itinerary sampling is needed for P1. The `network_spec.json` already encodes the movement schedule.

### 7.2 What P2 adds

P2 introduces movement after the initial evacuation: an infectious entity may take onward commercial flights from their arrival airport. For this you need:

1. The flight ledger from §6.
2. A connection buffer (minimum dwell time before an entity can board the next flight).
3. A bounded lookahead window (`max_odyssey_hours` — caps itinerary length to prevent unbounded graph traversal).
4. A termination rule: H or D state stops all outbound legs.

Implement this as a separate `build_itinerary(entity, gateway, snapshot_id, rng)` function that returns a list of `(departure_day, origin_iata, destination_iata)` tuples. The ABM day loop checks each entity's itinerary for departures on the current day and updates `location_node_id`. Transmission checks at cabin loci run only when two entities share the same `(flight_id_norm, departure_time_bucket)`.

---

## 8. Testing and validation

### 8.1 State machine tests

| Test | Checks |
|------|--------|
| `test_P_state_transmits_before_symptoms` | Agent in P state causes exposures; agent in E state does not |
| `test_H_state_no_outbound_legs` | After H transition, no itinerary legs are enqueued |
| `test_presym_ratio_sensitivity` | At k=0.2 and k=0.8, cumulative cases differ by expected factor |
| `test_mass_conservation` | Sum of state counts equals n_agents at every day |

### 8.2 Inference validation

Do not add new free parameters without running prior predictive checks first: sample 1000 draws from the proposed prior and verify the ensemble cumulative case distribution at day 14 contains the observed case count. If the observed count is in the tails (< 5th or > 95th percentile of simulated totals), the prior is miscalibrated before inference starts.

This check is cheap (no NUTS needed) and prevents investing in inference runs whose posteriors are dominated by misspecified priors.

### 8.3 Gateway allocation validation

Generate a synthetic fixture: 100 entities, network_spec with 60% to JNB and 40% to AMS, no pseudocount. Verify empirical draw frequencies converge to within 5% of the true weights at n=100. With pseudocount ε=0.5, verify both gateways have at least 1% allocation even when n_passengers for one is zero.

### 8.4 Snapshot ledger tests

| Test | Checks |
|------|--------|
| `test_snapshot_idempotent_insert` | Inserting same snapshot twice does not duplicate rows |
| `test_leg_mutation_blocked` | No UPDATE on flight_legs; amendments create new rows |
| `test_rerun_same_snapshot_id` | Given same `snapshot_id`, two ensemble runs produce identical aggregate counts |
| `test_degraded_flag_surfaces` | When `snapshot.status = "degraded"`, job metadata contains `degraded_flight_coverage: true` |

---

## 9. What this spec defers

| Item | Reason to defer |
|------|----------------|
| Hospital staff transmission pathways | Need staff population data and contact structure — not available |
| Airport community susceptible pool | Need patch population estimates and coupling calibration — see §5.3 |
| Near-real flight provider integration | Requires provider contract and license review — see §6.1 |
| MCMC over extended parameter vector | Currently not identifiable — fix parameters first, see §3.2 |
| Cabin CFD or seat-level contacts | Explicitly out of scope |
| Second-hop geographic risk from community transmission | Depends on §5.3 being resolved |

---

## 10. Decision log

These must be explicitly decided before implementation begins. Each is small but has downstream consequences.

| Decision | Options | Recommended | Why |
|----------|---------|-------------|-----|
| P duration (presymptomatic window) | Fixed vs inferred | Fixed at 2 days (mean) with Gamma(2, 1) shape | Not separately identified from onset data |
| β_pre/β_sym ratio k | Inferred vs fixed | Fixed at 0.5, sensitivity at 0.2 and 0.8 | Collinear with β_sym in daily counts |
| β_cabin | Inferred vs scenario | Scenario knob, default 0.05/contact-hour | No cabin-level outcome data available |
| Airport community susceptibles | Model vs skip | Skip for P1, document absence explicitly | No calibration data; see §5.2 |
| Laplace pseudocount ε | 0.1 to 1.0 | 0.5 per gateway in allowlist | Prevents lockout, minimal distortion |
| Static vs near-real flight data | Static community bundle vs API | Static for P1, API contract TBD | Ops decision not yet made; see §6.1 |
| Strict vs degraded abort on missing flights | Strict abort vs continue + flag | Continue with explicit flag for P1 | Strict abort blocks simulation entirely; degraded flag is surfaced to researcher |
