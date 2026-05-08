# Design: Production OD mobility from open/community data

**Date:** 2026-05-08  
**Status:** Approved — implementation plan: `docs/superpowers/plans/2026-05-11-production-mobility-od.md`  
**Constraint:** **Open/community data only** for the first real mobility bundle (no paid OAG/Cirium-class feeds in v1).

## Problem

`mobility_weekly_skeleton.json` proves the `MobilitySchedule` → metapop pipeline but uses a toy `patch_ids` graph. Real “risky airports emerge from outgoing schedules” needs a **versioned artifact** whose patches align with **IATA** (plus explicit seed patches such as `NSEED` / `SHIP`) and whose flows approximate **origin–destination passenger movement over time**.

## Goals

1. Replace the skeleton with a **reproducible bundle** built offline from **open-licensed** inputs.
2. Keep **runtime** simple: `EOSP_METAPOP_MOBILITY` points at a file; loaders produce the existing `MobilitySchedule` type.
3. Document **provenance** in API metadata so clients know the map is from a **static schedule snapshot**, not live ATC.

## Non-goals (v1)

- Real-time flight tracking as the primary mobility source (e.g. OpenSky live positions) for OD construction.
- Paid commercial aviation data products.
- Legal review beyond stating licenses in the bundle metadata (maintainer verifies compatibility before publishing a bundle).

## Open/community data stack (recommended)

Use **multiple** sources under permissive licenses; exact choice is an implementation detail of the ETL script.

| Role | Typical open sources | Caveats |
|------|---------------------|---------|
| Airport registry (IATA, lat/lng) | [OpenFlights](https://openflights.org/data.html) `airports.dat` (ODbL) | ODbL requires **derivative** data to stay ODbL if you mix; prefer **using it only inside private ETL** and publishing **EOSP’s own aggregated flow tables** under project license after legal check, or ship **user-provided** downloads unredistributed. **Safe pattern:** ETL README instructs user to download raw OpenFlights into `data/raw/` (gitignored); committed output is **derived aggregates** you vetted with counsel. |
| Scheduled routes / carriers | OpenFlights `routes.dat` | Gives **route** existence and often **stops**; **not** true OD demand. v1 approximates OD with **capacity proxies** (e.g. weekly frequency × seat estimate from equipment if available, else uniform default per route). |
| Optional enrichment | [OurAirports](https://ourairports.com/data/) (CC0 airport data) | Good for cross-checking IATA/ICAO; prefer CC0 where possible to reduce copyleft surface. |

**Honesty statement for v1:** Open/community route tables approximate **scheduled connectivity**, not ticketed OD or load factors. Uncertainty is handled downstream by **ensemble** parameters, not by pretending flows are exact.

## Architecture

### 1. Offline ETL (`scripts/build_mobility_bundle.py`)

- Inputs: paths to user-downloaded raw files (env or CLI args), simulation horizon (`n_days`), optional region filter (e.g. IATA set in Europe + Africa + medevac destinations from `network_spec.json`).
- Processing:
  - Build **directed** edges per logical day (repeat weekly pattern across horizon, or slice dated schedules if you add dated open data later).
  - Map **source_airport** / **dest_airport** (IATA) → internal `patch_ids` list = sorted unique IATA appearing on edges, **plus** reserved seed patches `NSEED` (and `SHIP` if used) **prepended** or **explicitly listed** in metadata.
  - Flow weight `n`: non-negative float; default = `frequency_per_week / 7 * seat_proxy` per day, or `1.0` per daily flight if seats unknown (document assumption).
- Output:
  - **Canonical file:** `mobility_bundle.parquet` (columns at minimum: `day`, `origin_iata`, `dest_iata`, `n`) **or** generated `mobility_bundle.json` matching existing v1 schema for drop-in compatibility.
  - **Sidecar:** `mobility_bundle.meta.json` — `schema_version`, `source_licenses[]`, `generated_at`, `horizon_days`, `assumptions[]`, `patch_id_order`.

### 2. Runtime loaders (`app/eosp/services/mobility.py`)

- `load_mobility_schedule(path: Path) -> MobilitySchedule` remains the public API.
- Add format dispatch:
  - `.json` — current v1 nested `days[].flows[]`.
  - `.parquet` — aggregate rows into the same `MobilitySchedule` in memory (pandas or pyarrow optional dependency; if missing, document “convert to JSON offline”).
- No network I/O in the loader for v1 (keeps API predictable and CI offline).

### 3. Patch alignment bridge

- Centralize **destination normalization** (e.g. `ZA_JNB` → `JNB`) in one module used by **`metapop_seed`** and by ETL when ingesting `network_spec.json` flight destinations.
- Warn when a **`network_spec`** destination has no matching patch in the bundle (flow zero until bundle includes that airport).

### 4. API metadata (`build_outbreak_geo` metapop path)

When `risk_model=metapop`, include:

- `mobility_bundle_version` / path basename
- `mobility_source`: short string, e.g. `openflights_routes_derived` + assumption note
- `mobility_license_note`: optional; copied from bundle sidecar so deployments can surface upstream terms (e.g. ODbL) without implying legal advice
- `mobility_date_range` or `mobility_horizon_days`

## Testing

- **Fixture:** Small Parquet or JSON (≤20 airports, 14 days) committed under `tests/fixtures/` built by the same ETL with `--sample`.
- **Tests:** Loader round-trip; no negative `n`; `total_outflow_per_patch_day` matches sum of outgoing edges per day.
- **CI:** No download of full OpenFlights in CI; use fixture only.

## Self-review (checklist)

- **Placeholders:** None; assumptions explicit where law/data varies.
- **Consistency:** Open-only constraint reflected; runtime remains file-based.
- **Scope:** Single implementation phase: ETL + loader + metadata + fixture tests. No federated APIs in v1.
- **Ambiguity:** “User downloads raw ODbL data locally” is the chosen pattern to avoid redistributing unclear derivatives; teams that need fully redistributable bundles should prioritize **CC0-only** inputs (OurAirports + synthetic flows) until counsel approves OpenFlights-derived aggregates.

## Approval gate

After implementation plan is written, implementation must not change this contract without revising this doc.
