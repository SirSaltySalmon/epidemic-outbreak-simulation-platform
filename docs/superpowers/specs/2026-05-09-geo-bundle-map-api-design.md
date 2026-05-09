# Design: Unified geo_bundle map API

**Date:** 2026-05-09
**Status:** Draft — supersedes map-assembly portions of `2026-05-09-geographic-abm-flight-design.md`
**Scope:** Track A — ship from the existing ABM without new flight data or state machine changes.

---

## 1. Problem statement

The dashboard map currently has two independent data paths feeding the same Leaflet layer:

1. `GET /geo/outbreak` returns `{ship, confirmed_cases, evacuation_flights, risk_heatmap, metadata}`.
2. The forecast cache (`ForecastResponse.metadata.geo_forecast`) returns a separate per-bucket percentile structure.

`map.js` receives both separately and merges them client-side. This means the frontend has to know which source wins, what schema each uses, and how to reconcile mismatches. Neither source carries a `schema_version`, so any field rename or shape change silently breaks rendering.

The second problem is that `geo.py` picks the **final simulation day's `infectious_I` median** as the heat score. At day 14 most agents have resolved to R or D, so the map frequently renders blank or near-blank. This is a known failure mode with no documented fallback metric.

---

## 2. Goals for this spec

- Define one backend-assembled payload (`geo_bundle`) that replaces both data paths.
- Fix the blank-map problem by changing the default heat metric to `cumulative_infected` (monotonic, never blank), with `infectious_I` peak available as a secondary layer.
- Add `schema_version` so breaking changes are detectable.
- Keep the existing `/geo/outbreak` endpoint working during transition behind a server-side adapter.
- Preserve all current provenance fields (`risk_source`, `risk_heatmap_explanation`).

**Not in scope:** new flight data, new epidemiological states, itinerary sampling, or near-real feeds. Those belong to Track B.

---

## 3. Metric choices and why

### 3.1 The blank-map failure

`infectious_I` on the last day is instantaneous: it is zero if the outbreak has resolved, low if it peaked on day 7. For a 14-day horizon on a ~150-agent cohort, most trajectories exhaust I within 10–12 days. The map goes blank.

### 3.2 Default metric: cumulative infected, final day

`cumulative_infected[final_day]` counts every agent that ever left S. It is monotonically non-decreasing by construction — it cannot be blank if any transmission occurred. The existing `bucket_cumulative_infected` array already computes this.

This is the right default for the question "which destinations are most implicated in this outbreak overall?"

### 3.3 Secondary layer: peak infectious, best day

For the question "where were active cases concentrated at peak?", select the simulation day with the highest total `infectious_I` summed across all buckets (on a per-trajectory basis, then take the ensemble median of the peak value). This avoids the final-day problem.

Implementation: add `peak_infectious_I` to `_aggregate_geo_forecast` output. For each trajectory, find `argmax(sum(bucket_infectious_I, axis=1))` (the peak day index), then record `bucket_infectious_I[peak_day, :]` for that trajectory. Stack across trajectories and take percentiles.

The computation is cheap — it is one extra reduction over the arrays already in memory during aggregation.

### 3.4 What stays the same

- The `by_day` structure in `geo_forecast` metadata is unchanged. Frontend features that consume daily bucket percentiles for the trend chart keep working.
- `cumulative_infected` and `infectious_I` per-day per-bucket percentiles stay in `by_day`.

---

## 4. geo_bundle schema

```json
{
  "schema_version": "1",
  "as_of_utc": "<ISO-8601>",
  "observed": {
    "case_markers": [
      {
        "country": "ZA",
        "lat": -26.1,
        "lng": 28.2,
        "confirmed": 3,
        "suspected": 1,
        "deaths": 1,
        "airport": "JNB"
      }
    ],
    "ingest_cursor": null
  },
  "simulation": {
    "ensemble_spec_hash": "<sha256 of n_simulations + rng_seed + scenario>",
    "inference_version": "<InferenceResult.version>",
    "n_simulations": 10000,
    "scenario": "baseline",
    "primary_layer_id": "cumulative_infected_median_final_day",
    "layers": [
      {
        "id": "cumulative_infected_median_final_day",
        "label": "Cumulative infections by destination (final day, median)",
        "metric_id": "cumulative_infected",
        "metric_day": "final",
        "metric_stat": "median",
        "features": [
          {
            "bucket_code": "ZA_JNB",
            "airport_iata": "JNB",
            "lat": -26.1,
            "lng": 28.2,
            "city": "Johannesburg",
            "country": "ZA",
            "value": 12.4,
            "ci_95_lower": 5.0,
            "ci_95_upper": 28.3
          }
        ]
      },
      {
        "id": "peak_infectious_I_median",
        "label": "Peak concurrent infectious agents by destination (ensemble median)",
        "metric_id": "peak_infectious_I",
        "metric_day": "peak",
        "metric_stat": "median",
        "features": []
      }
    ]
  },
  "provenance": {
    "risk_source": "abm_geo_forecast",
    "primary_metric_id": "cumulative_infected_median_final_day",
    "risk_heatmap_explanation": "ABM ensemble median cumulative infections per destination bucket on the final forecast day. Model-structured — not confirmed incidence.",
    "distinct_from": "Legacy OpenSky ring heat uses flight connectivity heuristics, not agent counts."
  },
  "evacuation_flights": [],
  "ship": {
    "lat": 20.5,
    "lng": -21.0,
    "name": "MV Hondius",
    "status": "en_route_tenerife"
  },
  "errors": []
}
```

Fields required for all versions:
- `schema_version` (string integer, increment on breaking shape changes)
- `simulation.primary_layer_id` must reference a valid entry in `simulation.layers`
- Each feature must carry `bucket_code`, `value`, `ci_95_lower`, `ci_95_upper`

Optional but recommended:
- `simulation.ensemble_spec_hash` (SHA-256 of `n_simulations|rng_seed|scenario|network_spec_hash`) — enables client-side cache invalidation without polling
- `simulation.inference_version` — ties display to the inference run that generated parameters

---

## 5. Backend changes

### 5.1 `_aggregate_geo_forecast` in `ensemble.py`

Add `peak_infectious_I` computation alongside the existing per-day loop:

```python
# Shape: (n_sim, n_buckets) — peak-day I per simulation per bucket
peak_day_per_sim = stacked_inf.sum(axis=2).argmax(axis=1)  # (n_sim,)
peak_inf_per_sim = stacked_inf[np.arange(n_sim), peak_day_per_sim, :]  # (n_sim, n_buckets)
```

Add a `"peak_infectious_I"` key to each bucket's output alongside `"cumulative_infected"` and `"infectious_I"`:

```python
buckets[label]["peak_infectious_I"] = _percentile_block(peak_inf_per_sim[:, bi].astype(float), include_50=True)
```

This is a single vectorised pass with no per-day loop overhead.

### 5.2 New `build_geo_bundle` function in `geo.py`

Add a function that assembles the full `geo_bundle` dict from:
- `cases: list[CaseRecord]` (for `observed.case_markers`)
- `geo_forecast: dict` from cached `ForecastResponse.metadata["geo_forecast"]`
- `inference_version: str`
- `n_simulations: int`
- `scenario: str`
- `coords: dict` from `load_airport_coords()`

The function picks `geo_forecast.by_day[-1].buckets` for the `cumulative_infected` layer features and the new `peak_infectious_I` for the peak layer, then assembles both into `geo_bundle.simulation.layers`. The ship bucket is always excluded from heat features.

`build_outbreak_geo` becomes a thin adapter:

```python
def build_outbreak_geo(...) -> dict[str, Any]:
    bundle = build_geo_bundle(...)
    # Extract legacy fields for backward compat
    primary_layer = next(l for l in bundle["simulation"]["layers"] if l["id"] == bundle["simulation"]["primary_layer_id"])
    return {
        "ship": bundle["ship"],
        "confirmed_cases": bundle["observed"]["case_markers"],
        "evacuation_flights": bundle["evacuation_flights"],
        "risk_heatmap": [
            {"airport_iata": f["airport_iata"], "lat": f["lat"], "lng": f["lng"],
             "city": f["city"], "country": f["country"],
             "risk_score": f["value"], "ring": 0}
            for f in primary_layer["features"]
        ],
        "metadata": {**bundle["provenance"], **bundle["simulation"]},
    }
```

This keeps existing callers working without change.

### 5.3 New `/geo/bundle` route (additive)

Add `GET /geo/bundle` that returns the full `geo_bundle` JSON. Same authorization as `/geo/outbreak`. This is what the frontend should eventually call directly.

During the transition, the existing `/geo/outbreak` continues to work from the adapter above.

### 5.4 `ensemble_spec_hash` computation

In `run_ensemble`, before returning, compute:

```python
import hashlib, json as _json
hash_input = f"{config.n_simulations}|{config.rng_seed}|{scenario.name}"
ensemble_spec_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
metadata["ensemble_spec_hash"] = ensemble_spec_hash
```

16 hex characters is sufficient for collision resistance at the scale of a single outbreak dashboard.

---

## 6. Frontend changes

### 6.1 Two-phase migration

**Phase 1 (current PR):** Keep `renderGeoData(geo, metadataGeoForecast)` intact. Add a check at the top:

```javascript
if (geo.schema_version) {
  _renderFromBundle(geo);
  return;
}
// existing dual-path logic below
```

`_renderFromBundle(bundle)` reads `bundle.simulation.layers`, finds the primary layer, and maps its features to the Leaflet heat layer. This is forward-compatible: if the server sends a `geo_bundle`-shaped response to the existing endpoint, the frontend handles it without the old path.

**Phase 2 (next release):** Call `/geo/bundle` directly. Remove the dual-path logic entirely.

### 6.2 Legend copy

When `provenance.risk_source == "abm_geo_forecast"`, the legend reads:

```
Model risk — cumulative infections per destination
ABM ensemble, 10 000 simulations
Not confirmed incidence
```

When `risk_source == "legacy_opensky_fallback"`, the legend reads:

```
Connectivity risk — flight ring heuristic
No cached simulation available
```

The legend text is driven from `provenance.risk_heatmap_explanation`, not hardcoded per kernel name.

### 6.3 Heat normalization

The current `abm_geo` path passes raw infectious counts to the Leaflet heat plugin. Cumulative infected counts will be larger in absolute terms. Use square-root normalization before passing to the plugin (same as metapop):

```javascript
function _normalizeForHeat(features) {
  const maxVal = Math.max(...features.map(f => f.value), 1);
  return features.map(f => [f.lat, f.lng, Math.sqrt(f.value / maxVal)]);
}
```

This is already the metapop behavior. Unify it under a shared `_normalizeForHeat` function used by all non-ring-based layers.

---

## 7. Fallback hierarchy

When ABM geo forecast is unavailable (no cached baseline or `by_day` is empty):

1. Check for `legacy_opensky_geo` in cached forecast metadata → serve cached ring heatmap.
2. If none, compute ring-1-only from live `p_transmit` → serve hub-only heatmap.
3. All fallbacks set `provenance.risk_source` to `"legacy_opensky_cached_simulation"`, `"legacy_opensky_ring1_only"`, or `"legacy_opensky_fallback"` respectively.
4. `errors` array in `geo_bundle` includes a structured entry: `{"code": "abm_geo_unavailable", "reason": "no_cached_baseline_geo_forecast", "fallback": "legacy_opensky_ring1_only"}`.

---

## 8. Validation

| Test | What it checks |
|------|----------------|
| `test_geo_bundle_schema_version` | Response carries `schema_version = "1"` |
| `test_geo_bundle_primary_layer_references_valid_id` | `primary_layer_id` exists in `layers[*].id` |
| `test_geo_bundle_cumulative_metric_is_never_blank` | With any non-zero seed, at least one feature has `value > 0` |
| `test_geo_bundle_peak_infectious_I_present` | `peak_infectious_I_median` layer exists and features have quantiles |
| `test_legacy_endpoint_compat` | `/geo/outbreak` returns same `risk_heatmap` shape as before |
| `test_fallback_produces_errors_array` | When no forecast cached, `geo_bundle.errors` includes `abm_geo_unavailable` entry |

---

## 9. What this does not fix

- **Dual kernels still exist** (metapop, legacy rings). This spec only establishes the bundle shape and fixes the blank-map metric. Kernel deprecation follows the `EOSP_METAPOP_ENABLED` flag already in place.
- **Flight data realism.** The bucket-to-airport mapping still comes from `network_spec.json` destinations, not a live flight ledger. Track B handles this.
- **Second-hop transmission.** Airports are still relay points; agents do not infect non-cohort susceptibles at airport patches. Track B handles this.
- **Itinerary timing.** The `depart_day` field in evacuation_flights is display-only. Track B handles movement-driven exposure.

---

## 10. Track B addendum — dynamic buckets and flight ledger (2026-05-09)

**Status:** Track B supersedes §2 “Track A only” for **simulation geography**: map and `geo_forecast` buckets are **no longer limited** to a fixed set of `network_spec.json` destination codes. They are driven by **case seeding**, **static or ingested flight schedules**, and **per-agent itineraries**.

### 10.1 Cardinality and identifiers

- **`features[]` length** is **not bounded** by three hubs. It includes every **bucket** (`bucket_code`) that appears in the **ensemble’s** `geo_forecast.by_day` for the cached baseline run (union of patches visited by any agent across simulations), plus optional static nodes such as `ship` when present in labels.
- **`bucket_code`** may be a **legacy cluster key** (e.g. `ZA_JNB`) or, in later phases, a **patch id** / **IATA-only** code; clients must treat `bucket_code` as an opaque display key and join with `airport_iata` / coordinates when provided.
- **`geo_forecast.bucket_order`** (when present) lists buckets for the **trajectory tensors** used in aggregation; its length is **variable** across runs if the world builder discovers more airports from the schedule or seed anchors.

### 10.2 Data dependencies (replacement for `network_spec.json`)

- **`network_spec.json` is deprecated** as the source of truth for **contact topology** and **evacuation legs**. Replace with:
  - **`spawn_profile.json`** — ship cohort size, UI stub, RNG seed, optional gateway priors.
  - **`flight_schedules_baseline.json`** (or DB snapshot + `flight_legs`) — canonical schedule rows; **`snapshot_id = SHA-256(canonical_json)`** for reproducibility and API metadata.
- **`evacuation_flights` in `geo_bundle`** may be derived from the **baseline schedule** for map arcs, omitted, or replaced by **case-weighted** overlays per product design.

### 10.3 `geo_bundle` / `geo_forecast` invariants under Track B

- **`schema_version`** remains the contract version for the **envelope** (`simulation.layers`, `observed`, `provenance`, `errors`). Breaking changes still require bumping `schema_version`.
- **Primary heat metric** remains **`cumulative_infected`** final-day medians (§3.2); **peak infectious** secondary layer unchanged (§3.3).
- **Fallback when ABM geo is missing** is **empty heat + structured `errors[]`** (no second kernel).

### 10.4 Metadata provenance

Forecast **`metadata`** should carry **`flight_snapshot_id`** (or equivalent) and a compact **`seed_manifest`** (counts per anchor airport / cohort) whenever Track B world building is used, so results stay auditable without `network_spec_hash`.
