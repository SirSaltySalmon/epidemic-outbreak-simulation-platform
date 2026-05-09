# Entity Geographic ABM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver **P1** of [docs/superpowers/specs/2026-05-09-entity-geographic-abm-design.md](docs/superpowers/specs/2026-05-09-entity-geographic-abm-design.md): canonical **place graph** from `network_spec.json`, **infectious-count–per–geo-bucket** time series from the existing vectorised ABM, **ensemble percentiles** exported alongside current cumulative-infected buckets, and an optional **`/geo/outbreak?risk_model=abm_geo`** path that builds the orange heat layer from **cached baseline forecast** metadata (single narrative: model-structured risk, not observed incidence). **P0** (metapop default-off) is already shipped—do not redo.

**Architecture:** Keep `ContactNetwork` and `simulate_trajectory` as the simulation core. Add **parallel accounting** `bucket_infectious_I` (agents in state **I** only, per `geo_bucket_spec` bucket) per simulation day. Extend `ensemble._aggregate_geo_forecast` to percentile-stack this array like existing `bucket_cumulative_infected`. Add a thin **`place_graph`** module for stable node ids (SHIP + IATA) and airport coordinate join for the geo API. Route **`abm_geo`** reads `ForecastResponse.metadata["geo_forecast"]` from the repository instead of running a second kernel.

**Tech Stack:** Python 3.11+, NumPy, SciPy sparse, FastAPI, existing `eosp.services.*` layout; pytest; Leaflet front-end in `app/eosp/static/js/map.js`.

**Context:** Prefer a [git worktree](https://git-scm.com/docs/git-worktree) for long implementation runs so `master` stays clean; not mandatory for small tasks.

---

## File structure (what gets touched)

| File | Responsibility |
|------|----------------|
| `app/eosp/services/place_graph.py` | **Create:** `PlaceGraph`, `build_place_graph_from_network_spec`, IATA list + SHIP node for coords join. |
| `tests/test_place_graph.py` | **Create:** Unit tests for graph nodes/edges from bundled spec. |
| `app/eosp/services/geo_buckets.py` | **Modify:** Add constants `GEO_BUCKET_METRIC_INFECTIOUS_I`, detail string; keep existing cumulative metric. |
| `app/eosp/services/abm.py` | **Modify:** `Trajectory` gains `bucket_infectious_I`; `_fill_bucket_infectious_I`; call each day in loop. |
| `tests/test_abm_geo_buckets.py` | **Create:** Seed infection on ship-only agents; assert infectious counts in ship bucket > 0 when I present. |
| `app/eosp/services/ensemble.py` | **Modify:** `_aggregate_geo_forecast` emits per-bucket percentiles for **both** metrics (`cumulative_infected` + `infectious_I` keys under each bucket or documented parallel dict—pick one schema and keep consistent). Update `distinct_from` text when legacy heat not sole overlay. |
| `tests/test_ensemble_geo_forecast.py` | **Create:** Small network + two traj with known bucket arrays → aggregated JSON shape. |
| `app/eosp/services/geo.py` | **Modify:** Branch `risk_model == "abm_geo"` calling new `_heatmap_from_forecast_geo` using `place_graph` + `load_airport_coords`. |
| `app/eosp/api/routes.py` | **Modify:** `geo_outbreak`: allow `abm_geo`; load baseline cached forecast via `get_cached_forecast`; pass into `build_outbreak_geo` or assemble after. |
| `app/eosp/core/settings.py` | **Modify:** Document allowed `geo_risk_model` values including `abm_geo`. |
| `.env.example` | **Modify:** Comment example `EOSP_GEO_RISK_MODEL=abm_geo`. |
| `app/eosp/static/js/map.js` | **Modify:** Legend + heat normalization when `metadata.risk_source == "abm_geo_forecast"`. |
| `tests/test_api.py` | **Modify:** Test `abm_geo` returns expected risk_source and requires cached forecast or graceful fallback. |

---

### Task 1: Place graph builder

**Files:**
- Create: `app/eosp/services/place_graph.py`
- Create: `tests/test_place_graph.py`
- Modify: `app/eosp/services/patch_codes.py` — **only if** you need a helper already missing (reuse `iata_from_destination`).

- [ ] **Step 1: Write failing test**

Create `tests/test_place_graph.py`:

```python
from pathlib import Path

from eosp.services.network import load_spec
from eosp.services.place_graph import build_place_graph_from_network_spec


def test_place_graph_includes_ship_and_destination_iatas():
    spec_path = Path(__file__).resolve().parents[2] / "app" / "eosp" / "data" / "network_spec.json"
    spec = load_spec(spec_path)
    g = build_place_graph_from_network_spec(spec)
    assert "SHIP" in g.node_ids
    assert "JNB" in g.node_ids  # from ZA_JNB
    assert "AMS" in g.node_ids
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_place_graph.py::test_place_graph_includes_ship_and_destination_iatas -v`

Expected: `ImportError` or `ModuleNotFoundError` for `place_graph`.

- [ ] **Step 3: Implement minimal module**

Create `app/eosp/services/place_graph.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eosp.services.patch_codes import iata_from_destination


@dataclass(frozen=True)
class PlaceGraph:
    """Canonical map nodes for P1 (SHIP + medevac airport IATAs)."""

    node_ids: tuple[str, ...]


def build_place_graph_from_network_spec(spec: dict[str, Any]) -> PlaceGraph:
    codes: set[str] = {"SHIP"}
    for flight in spec.get("flights", []):
        dest = flight.get("destination")
        if dest:
            codes.add(iata_from_destination(str(dest)))
    for dest_entry in spec.get("destinations", []):
        dest = dest_entry.get("destination")
        if dest:
            codes.add(iata_from_destination(str(dest)))
    ordered = ("SHIP",) + tuple(sorted(codes - {"SHIP"}))
    return PlaceGraph(node_ids=ordered)
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest tests/test_place_graph.py -v`

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add app/eosp/services/place_graph.py tests/test_place_graph.py
git commit -m "feat(place-graph): SHIP and medevac IATA nodes from network_spec"
```

---

### Task 2: Infectious-only bucket accounting in ABM

**Files:**
- Modify: `app/eosp/services/abm.py`
- Create: `tests/test_abm_geo_buckets.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_abm_geo_buckets.py`:

```python
import numpy as np

from eosp.services.abm import STATE_I, SeedState, simulate_trajectory
from eosp.services.network import build_default_network


def test_trajectory_exposes_bucket_infectious_I_shape():
    net = build_default_network(n_days=5)
    n_agents = net.n_agents
    seed = SeedState(
        exposed=[],
        infectious=list(range(min(3, n_agents))),
        recovered=[],
        deceased=[],
    )
    traj = simulate_trajectory(
        net,
        params={
            "p_transmit": 0.01,
            "contacts_daily": 2.0,
            "incubation_mean": 5.0,
            "h2h_multiplier": 1.0,
            "cfr": 0.1,
        },
        seed=seed,
        n_days=5,
        rng=np.random.default_rng(42),
    )
    assert traj.bucket_infectious_I is not None
    assert traj.bucket_infectious_I.shape == (6, len(traj.geo_bucket_labels))
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_abm_geo_buckets.py::test_trajectory_exposes_bucket_infectious_I_shape -v`

Expected: `AttributeError: bucket_infectious_I`.

- [ ] **Step 3: Implement in ABM**

In `app/eosp/services/abm.py`:

1. Add to `@dataclass Trajectory`:

```python
    bucket_infectious_I: np.ndarray | None = None  # shape (n_days + 1, n_buckets)
```

2. After allocating `bucket_cumulative_infected`, allocate:

```python
    bucket_infectious_I_arr = np.zeros((n_days + 1, n_geo_buckets), dtype=np.int32)
```

3. Add function next to `_fill_bucket_infected`:

```python
def _fill_bucket_infectious_I(
    state: np.ndarray,
    agent_bucket: np.ndarray,
    out: np.ndarray | None,
    day: int,
) -> None:
    if out is None:
        return
    infectious_mask = state == STATE_I
    n_b = out.shape[1]
    for b in range(n_b):
        mask = infectious_mask & (agent_bucket == b)
        out[day, b] = int(mask.sum())
```

4. Call `_fill_bucket_infectious_I(..., bucket_infectious_I_arr, 0)` after first `_fill_bucket_infected`; same inside the day loop before loop end; pass `bucket_infectious_I_arr` into `Trajectory(...)`.

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest tests/test_abm_geo_buckets.py -v`

- [ ] **Step 5: Commit**

```bash
git add app/eosp/services/abm.py tests/test_abm_geo_buckets.py
git commit -m "feat(abm): per-bucket infectious (I) counts by simulation day"
```

---

### Task 3: geo_buckets metric constants

**Files:**
- Modify: `app/eosp/services/geo_buckets.py`

- [ ] **Step 1: Add constants**

Append:

```python
GEO_BUCKET_METRIC_INFECTIOUS_I_ID = "infectious_present"
GEO_BUCKET_METRIC_INFECTIOUS_I_DETAIL = (
    "Agents in the bucket in state I (infectious) only."
)
```

- [ ] **Step 2: Commit**

```bash
git add app/eosp/services/geo_buckets.py
git commit -m "docs(geo-buckets): metric id for infectious-only map export"
```

---

### Task 4: Ensemble aggregation for infectious_I

**Files:**
- Modify: `app/eosp/services/ensemble.py`
- Create: `tests/test_ensemble_geo_forecast.py`

**Schema choice (lock this everywhere):** Under each day in `by_day`, each bucket label maps to an object with keys `"cumulative_infected"` and `"infectious_I"` where each value is the same percentile dict shape as today (`median`, `ci_95_lower`, …).

- [ ] **Step 1: Write failing test**

Create `tests/test_ensemble_geo_forecast.py` with a minimal fake `Trajectory` list (two trajectories, same labels, numpy stacks for both `bucket_cumulative_infected` and `bucket_infectious_I`) and assert `_aggregate_geo_forecast` returns both keys for `"ship"` on day 1.

Import `_aggregate_geo_forecast` from ensemble (if private, test via `run_ensemble` with monkeypatched `simulate_trajectory`—prefer testing private function only if project already does; otherwise use thin wrapper).

Simplest path: duplicate percentile logic test by calling `run_ensemble` with tiny `n_simulations` and stub network—heavy. **Recommended:** export `_aggregate_geo_forecast` in tests via `from eosp.services.ensemble import _aggregate_geo_forecast` (keep function module-private but Python allows).

Stub trajectories:

```python
from dataclasses import dataclass
from datetime import date

import numpy as np

from eosp.services.abm import Trajectory
from eosp.services.ensemble import _aggregate_geo_forecast


def test_aggregate_geo_forecast_includes_infectious_I():
    labels = ("ship", "ZA_JNB")
    z = np.zeros
    t0 = Trajectory(
        n_days=2,
        daily_counts=z((3, 5), dtype=np.int32),
        cumulative_cases=z(3, dtype=np.int32),
        cumulative_deaths=z(3, dtype=np.int32),
        new_cases_per_day=z(3, dtype=np.int32),
        geo_bucket_labels=labels,
        bucket_cumulative_infected=np.array([[0, 0], [2, 0], [2, 1]], dtype=np.int32),
        bucket_infectious_I=np.array([[0, 0], [1, 0], [0, 1]], dtype=np.int32),
    )
    t1 = Trajectory(
        n_days=2,
        daily_counts=z((3, 5), dtype=np.int32),
        cumulative_cases=z(3, dtype=np.int32),
        cumulative_deaths=z(3, dtype=np.int32),
        new_cases_per_day=z(3, dtype=np.int32),
        geo_bucket_labels=labels,
        bucket_cumulative_infected=np.array([[0, 0], [4, 0], [4, 2]], dtype=np.int32),
        bucket_infectious_I=np.array([[0, 0], [2, 0], [1, 0]], dtype=np.int32),
    )
    out = _aggregate_geo_forecast(
        trajectories=[t0, t1],
        start_date=date(2026, 5, 7),
        n_days=2,
    )
    day1 = out["by_day"][0]
    assert "cumulative_infected" in day1["buckets"]["ship"]
    assert "infectious_I" in day1["buckets"]["ship"]
```

Adjust assertions to match actual nested structure you implement.

- [ ] **Step 2: Run test — FAIL**

Run: `pytest tests/test_ensemble_geo_forecast.py -v`

- [ ] **Step 3: Implement `_aggregate_geo_forecast` stacking**

In `ensemble.py`, after stacking `bucket_cumulative_infected`, also stack `bucket_infectious_I` when present on all trajectories (if any traj lacks it, skip infectious block entirely for backward compat).

For each day and bucket, compute `_percentile_block` on the infectious slice.

Nest results under each bucket key as described.

Update `metadata["geo_forecast"]` embed in `run_ensemble` unchanged path—automatic once `_aggregate_geo_forecast` returns richer structure.

Set `metric_detail` in output to mention both sub-metrics or use a small `metrics` list in the dict:

```python
"metrics": [
    {"id": GEO_BUCKET_METRIC_ID, "detail": GEO_BUCKET_METRIC_DETAIL},
    {"id": GEO_BUCKET_METRIC_INFECTIOUS_I_ID, "detail": GEO_BUCKET_METRIC_INFECTIOUS_I_DETAIL},
]
```

Import new constants from `geo_buckets`.

- [ ] **Step 4: Run full ensemble-related tests**

Run: `pytest tests/test_metapop.py tests/test_abm_geo_buckets.py tests/test_ensemble_geo_forecast.py -v`

Also: `pytest tests/test_api.py -q --tb=no -k forecast` if exists.

- [ ] **Step 5: Commit**

```bash
git add app/eosp/services/ensemble.py tests/test_ensemble_geo_forecast.py
git commit -m "feat(ensemble): percentile geo forecast for infectious_I per bucket"
```

---

### Task 5: ABM geo heatmap assembler

**Files:**
- Modify: `app/eosp/services/geo.py`
- Modify: `app/eosp/services/place_graph.py` — add `heatmap_points_from_abm_geo_forecast(...)`

- [ ] **Step 1: Write failing test in `tests/test_geo_abm_heatmap.py`**

Pass a fake `geo_forecast` dict (single day, two buckets `ZA_JNB` with infectious_I median 5) and assert `_heatmap_rows_from_abm_geo` returns list of dicts with `risk_score`, `airport_iata`, lat/lng when coords exist.

Use real `load_airport_coords()` for JNB if available in test env or mock coords dict `{"JNB": {"lat": -26.1, "lng": 28.2, "city": "Johannesburg", "country": "ZA"}}`.

- [ ] **Step 2: Implement helper**

In `geo.py` or `place_graph.py`, implement mapping from bucket code `ZA_JNB` → IATA via `iata_from_destination`, then coords.

Heatmap `risk_score` for day **horizon**: use **median infectious_I on last simulation day** (match design option “expected infectious present at end of horizon”), normalize for leaflet like metapop (`sqrt` scaling) in **frontend** only—API returns raw median float.

Add metadata:

```python
"risk_source": "abm_geo_forecast",
"risk_metric_id": "infectious_present_median_final_day",
"risk_heatmap_explanation": (
    "ABM ensemble median count of infectious (I) agents per destination bucket on the final "
    "forecast day — model-structured, not reported incidence."
),
```

- [ ] **Step 3: Wire `build_outbreak_geo`**

Add parameters:

```python
def build_outbreak_geo(
    ...
    abm_geo_forecast: dict[str, Any] | None = None,
```

When `risk_model == "abm_geo"` and `abm_geo_forecast` is None, raise `ValueError` or return legacy with metadata flag `abm_geo_unavailable: true`—**prefer** explicit unavailable payload so UI can show message. Design choice: return 200 with legacy fallback + `metadata.abm_geo_fallback_reason`.

Document chosen behaviour in test.

- [ ] **Step 4: Commit**

```bash
git add app/eosp/services/geo.py tests/test_geo_abm_heatmap.py app/eosp/services/place_graph.py
git commit -m "feat(geo): build outbreak heatmap rows from ABM geo_forecast metadata"
```

---

### Task 6: Route integration

**Files:**
- Modify: `app/eosp/api/routes.py`
- Modify: `app/eosp/core/settings.py`
- Modify: `.env.example`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Extend allowed risk models**

In `routes.geo_outbreak`, after normalizing `effective_risk`, if value is `abm_geo`:

```python
from eosp.services.forecast import ForecastNotCachedError, get_cached_forecast

abm_payload: dict | None = None
if effective_risk == "abm_geo":
    try:
        fc = get_cached_forecast("baseline", repository=repo, inference=inference)
        abm_payload = fc.metadata.get("geo_forecast") if fc else None
    except ForecastNotCachedError:
        abm_payload = None
```

Pass `abm_geo_forecast=abm_payload` into `build_outbreak_geo`. If missing, use documented fallback.

- [ ] **Step 2: Settings**

Add comment in `settings.py` near `geo_risk_model`: allowed values `legacy`, `metapop` (if enabled), `abm_geo`.

- [ ] **Step 3: Tests**

Add `test_geo_outbreak_abm_geo_uses_forecast_metadata(monkeypatch)` with cached forecast in repo containing `geo_forecast`; GET `?risk_model=abm_geo`; assert `risk_source == "abm_geo_forecast"`.

- [ ] **Step 4: Commit**

```bash
git add app/eosp/api/routes.py app/eosp/core/settings.py .env.example tests/test_api.py
git commit -m "feat(api): geo/outbreak abm_geo risk_model from cached baseline forecast"
```

---

### Task 7: Frontend legend and heat behaviour

**Files:**
- Modify: `app/eosp/static/js/map.js`

- [ ] **Step 1: Add constant**

```javascript
const ABM_GEO_RISK_SOURCE = "abm_geo_forecast";
```

- [ ] **Step 2: Branch `_isMetapopKernel` or add `_isAbmGeoKernel`**

Normalize heat points: use same sqrt normalization as metapop when kernel uses raw counts.

- [ ] **Step 3: Update `_updateMapLegend`**

When `abm_geo_forecast`, legend explains orange layer from ABM infectious median.

- [ ] **Step 4: Manual smoke**

Run app, load dashboard with cached baseline; toggle env `EOSP_GEO_RISK_MODEL=abm_geo` if wired to default query—verify map renders.

- [ ] **Step 5: Commit**

```bash
git add app/eosp/static/js/map.js
git commit -m "feat(ui): map legend for abm_geo_forecast heat source"
```

---

### Task 8: Regression sweep

- [ ] **Step 1: Run full pytest**

Run: `python -m pytest`

Expected: all green; fix any Trajectory call sites missing new field (default `None`).

- [ ] **Step 2: Commit fixes only if needed**

```bash
git commit -am "fix: Trajectory defaults for infectious_I across tests"
```

---

## Phase 2 (mobility-driven second hop) — preview tasks

Implement **after** P1 is merged. Same plan file discipline; create follow-on plan `2026-05-09-entity-geographic-abm-p2.md` if P2 exceeds 400 lines.

| Step | Deliverable |
|------|-------------|
| P2-1 | Extend `ContactNetwork` or parallel structure with **inter-airport** edges from `MobilitySchedule` for days after evacuation. |
| P2-2 | **Relocate agents:** finite-state machine moves agent indices between buckets on scheduled leg days (starts with infectious/exposed travellers only). |
| P2-3 | **Regenerate** adjacency or use layered daily matrices for airport clusters. |
| P2-4 | Parity test: metapop-enabled run vs ABM extended run on toy 4-patch graph (statistical tolerance). |

---

## Self-review (plan vs spec)

| Spec section | Covered by |
|--------------|------------|
| §3.1 Place graph | Task 1 |
| §3.1 Outputs geo metric documented | Tasks 4–5 (`risk_metric_id`) |
| §3.3 Migration from buckets | Tasks 2–4 (additive arrays) |
| §4 Geo assembler | Tasks 5–6 |
| §4 Frontend | Task 7 |
| §6 P1 row | Tasks 1–8 |
| §7 Validation | Tests each task |
| §8 Runtime risk | Task 8 sweep; P2 defers scaling |

**Placeholder scan:** No TBD steps; P2 uses table preview only—full code deferred to separate plan file.

**Type consistency:** `bucket_infectious_I` always `(n_days+1, n_buckets)` when present; `geo_bucket_labels` unchanged.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-09-entity-geographic-abm.md`. Two execution options:**

**1. Subagent-driven (recommended)** — Dispatch a fresh subagent per task; review between tasks; use **superpowers:subagent-driven-development**.

**2. Inline execution** — Run tasks sequentially in this session with checkpoints; use **superpowers:executing-plans**.

**Which approach do you want?**
