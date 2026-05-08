# Production OD mobility bundle — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an offline ETL that builds mobility bundles from **open/community** route data (OpenFlights-style CSV), extend runtime loaders for **Parquet** (optional `pyarrow`) and **JSON**, centralize IATA normalization, surface **bundle provenance** in metapop geo metadata, and add **CI-safe fixtures** (no live downloads).

**Architecture:** Raw inputs stay **gitignored** under `data/raw/openflights/`; the ETL writes `mobility_bundle.json` + `mobility_bundle.meta.json` (and optional `.parquet`) into `app/eosp/data/bundles/` or a path the operator chooses. `load_mobility_schedule(path)` dispatches on extension. `build_outbreak_geo` reads the sidecar when present for API metadata.

**Tech stack:** Python 3.11+, NumPy (existing), optional `pyarrow` in `[project.optional-dependencies] mobility`, stdlib `csv` for ETL (avoid pandas in v1 if possible).

**Design reference:** `docs/superpowers/specs/2026-05-08-production-mobility-od-design.md`

---

## File map

| Path | Responsibility |
|------|------------------|
| `app/eosp/services/patch_codes.py` | `iata_from_destination(code: str) -> str` (single source of truth). |
| `app/eosp/services/metapop_seed.py` | Import helper from `patch_codes`; remove duplicated `_iata_from_destination`. |
| `app/eosp/services/mobility.py` | `load_mobility_from_parquet`, dispatch in `load_mobility_schedule`. |
| `app/eosp/scripts/build_mobility_bundle.py` | CLI ETL: routes → daily flows + meta + optional parquet. |
| `app/eosp/data/bundles/.gitkeep` | Directory for generated bundles (actual bundles optional in repo). |
| `data/raw/openflights/.gitkeep` | Placeholder; document downloads in script `--help`. |
| `.gitignore` | Ignore `data/raw/` contents. |
| `pyproject.toml` | Optional extra `mobility = ["pyarrow>=15.0"]`. |
| `tests/fixtures/mobility_minimal.json` | Small multi-day JSON (committed) mirroring production shape. |
| `tests/fixtures/mobility_minimal.meta.json` | Sidecar for metadata merge tests. |
| `tests/test_mobility.py` | Loader invariants + parquet tests behind `importorskip`. |
| `tests/test_geo_metapop_metadata.py` or extend `test_api.py` | Metapop metadata includes mobility fields when sidecar provided. |
| `app/eosp/services/geo.py` | Load `.meta.json` sibling; inject metadata keys. |

---

### Task 1: `patch_codes` + refactor `metapop_seed`

**Files:**
- Create: `app/eosp/services/patch_codes.py`
- Modify: `app/eosp/services/metapop_seed.py`

- [ ] **Step 1: Failing import test** — `from eosp.services.patch_codes import iata_from_destination` then `assert iata_from_destination("ZA_JNB") == "JNB"`.

```python
# tests/test_patch_codes.py
from eosp.services.patch_codes import iata_from_destination


def test_iata_from_destination_strips_country_prefix():
    assert iata_from_destination("ZA_JNB") == "JNB"
    assert iata_from_destination("AMS") == "AMS"
```

- [ ] **Step 2: Implement** `patch_codes.py`:

```python
def iata_from_destination(destination: str) -> str:
    d = (destination or "").strip()
    if "_" in d:
        return d.rsplit("_", 1)[-1]
    return d
```

- [ ] **Step 3: Refactor** `metapop_seed.py`: delete local `_iata_from_destination`, use `from eosp.services.patch_codes import iata_from_destination`.

- [ ] **Step 4:** `python -m pytest tests/test_patch_codes.py tests/test_metapop_seed.py -v` — expect all pass.

- [ ] **Step 5: Commit** — `refactor(metapop): centralize IATA destination normalization`

---

### Task 2: Parquet loader + `load_mobility_schedule` dispatch

**Files:**
- Modify: `app/eosp/services/mobility.py`
- Modify: `pyproject.toml` (optional-dependencies `mobility`)
- Modify: `tests/test_mobility.py`

- [ ] **Step 1: Implement** in `mobility.py`:

```python
def load_mobility_schedule(path: Path) -> MobilitySchedule:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return _load_mobility_parquet(path)
    if suffix == ".json":
        return _load_mobility_json(path)
    raise ValueError(f"Unsupported mobility file type: {suffix}")
```

Move current JSON body into `_load_mobility_json`. Add `_load_mobility_parquet` using `pyarrow.parquet.read_table` expecting columns `day`, `origin_iata`, `dest_iata`, `n` (all `n >= 0`).

**v1 rule (patch order):** Canonical production artifact is **JSON** with `patch_ids[0] == "NSEED"`. Parquet is optional. When loading `.parquet`, **`patch_ids` must come from** Parquet file metadata key `eosp.patch_ids` (UTF-8 comma-separated list, first entry must be `NSEED` for compatibility with `metapop_seed`). The ETL in Task 3 writes this metadata when it emits `.parquet`. If metadata is missing, raise `ValueError` with a clear message to use JSON or regenerate the bundle.

- [ ] **Step 2: Test JSON still works** — existing `test_load_mobility_schedule_builds_patch_index` unchanged path `.json`.

- [ ] **Step 3: Parquet test** (skip if no pyarrow):

```python
import pytest

pq = pytest.importorskip("pyarrow")
# build tiny table in-memory, write temp path, load, assert schedule.n_days
```

- [ ] **Step 4: Commit** — `feat(mobility): dispatch json|parquet loaders with optional pyarrow`

---

### Task 3: ETL script `build_mobility_bundle.py`

**Files:**
- Create: `app/eosp/scripts/build_mobility_bundle.py`
- Create: `data/raw/openflights/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Extend .gitignore**

```
data/raw/
```

- [ ] **Step 2: CLI** — argparse: `--routes` (path to `routes.dat` or CSV), `--airports` (optional filter: only airports appearing in both), `--n-days` int default 14, `--out-json` path, `--out-parquet` optional path, `--default-seat-proxy` float default 150, `--weekly-to-daily` divide frequency by 7.

**Parsing:** OpenFlights `routes.dat` is CSV: use `csv.reader`. Columns index: `Airline`, `Airline ID`, `Src airport`, `Src airport ID`, `Dest airport`, `Dest airport ID`, `Codeshare`, `Stops`, `Equipment`. **Stops** ignored in v1; skip rows with `\\N` or empty src/dest.

**Flow `n` per day:** For each route, `n = max(1, round((weekly_equiv / 7) * seat_proxy))` where `weekly_equiv = 7` if unknown (one daily flight assumed) — document v1 crude default; optional: count duplicate route rows as frequency.

- [ ] **Step 3: Output JSON** — `patch_ids`: `("NSEED",) + tuple(sorted(iatas))` per spec (prepend seed). **`days`**: for each `d in range(n_days)`, **same** flow set (weekly repetition) OR slight variation — v1 **repeat identical** weekly aggregate / 7 per day from aggregated edges.

- [ ] **Step 4: Write `mobility_bundle.meta.json`** alongside out-json basename (e.g. `out.meta.json` if `out.json`):

```json
{
  "schema_version": 1,
  "mobility_source": "openflights_routes_derived",
  "source_licenses": ["OpenFlights ODbL (user-supplied raw)"],
  "generated_at": "<iso8601>",
  "horizon_days": 14,
  "assumptions": ["routes approximated as daily OD capacity proxy", "..."],
  "patch_id_order": ["NSEED", "AMS", ...]
}
```

- [ ] **Step 4b (optional Parquet):** If `--out-parquet` is set, write columns `day`, `origin_iata`, `dest_iata`, `n` and set Arrow schema metadata `eosp.patch_ids` to the comma-joined `patch_ids` list (same order as JSON, `NSEED` first).

- [ ] **Step 5: Smoke test** — add `if __name__` guard; test with **inline** minimal routes CSV in `tests/test_build_mobility_bundle_smoke.py` (two airports AMS→JNB).

```python
def test_etl_builds_json_with_nseed(tmp_path):
    routes = tmp_path / "routes.csv"
    routes.write_text("X,1,AMS,1,JNB,0,Y,0,738\n", encoding="utf-8")
    out = tmp_path / "out.json"
    # subprocess or import main() builder
    ...
    data = json.loads(out.read_text())
    assert data["patch_ids"][0] == "NSEED"
    assert "JNB" in data["patch_ids"]
```

- [ ] **Step 6: Commit** — `feat(scripts): build mobility bundle from OpenFlights-style routes`


---

### Task 4: Geo metadata from sidecar

**Files:**
- Modify: `app/eosp/services/geo.py`
- Create: `tests/fixtures/mobility_minimal.meta.json`
- Modify: `tests/test_api.py` or new test file

- [ ] **Step 1: Helper** `load_mobility_sidecar_meta(mobility_path: Path) -> dict | None` — if `mobility_path.with_suffix(".meta.json").exists()`, return `json.load`, else if stem `foo.parquet` look for `foo.meta.json`.

- [ ] **Step 2:** In `build_outbreak_geo` metapop branch, after loading schedule, merge keys into `metadata`: `mobility_bundle_version` (basename of mobility file), copy `mobility_source`, `mobility_license_note`, `horizon_days` as `mobility_horizon_days` from sidecar when present.

- [ ] **Step 3: Fixture sidecar** minimal + test metapop GET returns `mobility_horizon_days` when using fixture JSON path and adjacent meta.

- [ ] **Step 4: Commit** — `feat(geo): metapop metadata from mobility bundle sidecar`

---

### Task 5: Documentation + developer ergonomics

**Files:**
- Modify: `docs/superpowers/specs/2026-05-08-production-mobility-od-design.md` status line to `Implemented` (after code lands) **or** add `app/eosp/scripts/README-mobility.md` (user prefers minimal docs — add short section to existing `README.md` only if already documents dev setup).

- [ ] **Step 1:** Add **10-line** subsection under README or `docs/superpowers/specs/` pointer: download links, `python -m app.eosp.scripts.build_mobility_bundle` invocation example (adjust module path to how package is run: `python -m eosp.scripts.build_mobility_bundle` with `--app-dir app` may be needed).

Verify run pattern matches repo: scripts live under `app/eosp/scripts`; run as:

`python -m eosp.scripts.build_mobility_bundle --routes data/raw/openflights/routes.dat --out-json app/eosp/data/bundles/mobility_openflights.json`

- [ ] **Step 2: Commit** — `docs: how to build open-data mobility bundle`

---

## Self-review

| Spec section | Task |
|--------------|------|
| Offline ETL | Task 3 |
| Loaders json+parquet | Task 2 |
| Patch bridge | Task 1 |
| API metadata | Task 4 |
| Fixtures / CI | Tasks 2–3 |
| Open-only | Task 3 docs + meta `source_licenses` |

**Placeholder scan:** none.

---

## Execution handoff

**Plan saved to:** `docs/superpowers/plans/2026-05-11-production-mobility-od.md`

1. **Subagent-driven** — one subagent per task.  
2. **Inline** — `executing-plans` in this session.

Which approach?
