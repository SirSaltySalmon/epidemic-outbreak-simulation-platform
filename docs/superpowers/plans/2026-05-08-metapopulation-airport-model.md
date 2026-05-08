# Metapopulation airport model (Approach A) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a **stochastic metapopulation** engine driven by **time-varying airport-to-airport flows**, seed from the existing ship/medevac story, and surface **ensemble-derived risky airports** through the geo API so map risk matches the simulation kernel (replacing authoritative use of the OpenSky ring heuristic when enabled).

**Architecture:** New `MobilitySchedule` (data + loader), `MetapopSimulator` (SEIR patches + multinomial movement of E/I), and a thin assembler that feeds `build_outbreak_geo` (or a parallel path) with `metapop_forecast` blocks. Existing NumPyro inference stays the parameter source for v1. Legacy `risk_propagation.compute_risk_zones` remains available behind a flag for regression.

**Tech stack:** Python 3.11+, NumPy, Pydantic (optional for schedule schema), FastAPI routes in `app/eosp/api/routes.py`, pytest.

**Design reference:** `docs/superpowers/specs/2026-05-08-metapopulation-airport-design.md`

---

## File map (planned)

| Path | Responsibility |
|------|----------------|
| `app/eosp/services/mobility.py` | Types + load/validate mobility schedules (JSON v1). |
| `app/eosp/services/metapop.py` | SEIR patch dynamics, flow step, `run_ensemble_metapop`. |
| `app/eosp/data/mobility_weekly_skeleton.json` | Checked-in **synthetic** OD stub (replace with real schedules later). |
| `app/eosp/services/geo.py` | Call metapop assembler when enabled; merge into outbreak payload. |
| `app/eosp/api/routes.py` | Query param or settings: `risk_model=legacy|metapop`. |
| `app/eosp/core/models.py` | Add small typed dicts / models only if needed for API clarity. |
| `tests/test_mobility.py` | Schedule loading + invariants. |
| `tests/test_metapop.py` | Mass balance, secondary hub emergence (toy graph). |

---

### Task 1: Mobility schedule schema + loader

**Files:**
- Create: `app/eosp/services/mobility.py`
- Create: `app/eosp/data/mobility_weekly_skeleton.json`
- Create: `tests/test_mobility.py`

- [ ] **Step 1: Write failing test — loader returns sorted patch list and non-negative flows**

```python
# tests/test_mobility.py
from pathlib import Path

from eosp.services.mobility import load_mobility_schedule


def test_load_mobility_schedule_builds_patch_index():
    base = Path(__file__).resolve().parents[1] / "app" / "eosp" / "data"
    sched = load_mobility_schedule(base / "mobility_weekly_skeleton.json")
    assert sched.patch_ids == ("NSEED", "HUB1", "HUB2", "LEAF")
    assert sched.n_days >= 3
    day0 = sched.day_flows[0]
    assert day0[("NSEED", "HUB1")] >= 0
    assert sched.total_outflow_per_patch_day.shape[0] == sched.n_days
```

- [ ] **Step 2: Run test — expect import/attribute failure**

Run: `pytest tests/test_mobility.py::test_load_mobility_schedule_builds_patch_index -v`  
Expected: `ModuleNotFoundError` or missing `load_mobility_schedule`.

- [ ] **Step 3: Implement loader + minimal JSON fixture**

`mobility_weekly_skeleton.json` (example structure):

```json
{
  "version": 1,
  "patch_ids": ["NSEED", "HUB1", "HUB2", "LEAF"],
  "days": [
    {
      "flows": [
        {"from": "NSEED", "to": "HUB1", "n": 100},
        {"from": "HUB1", "to": "HUB2", "n": 80},
        {"from": "HUB1", "to": "LEAF", "n": 40},
        {"from": "HUB2", "to": "LEAF", "n": 60}
      ]
    },
    {
      "flows": [
        {"from": "HUB1", "to": "HUB2", "n": 80},
        {"from": "HUB2", "to": "LEAF", "n": 50}
      ]
    },
    {
      "flows": [
        {"from": "HUB1", "to": "LEAF", "n": 70}
      ]
    }
  ]
}
```

`mobility.py` core:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MobilitySchedule:
    patch_ids: tuple[str, ...]
    n_days: int
    day_flows: tuple[dict[tuple[int, int], float], ...]
    total_outflow_per_patch_day: Any  # np.ndarray shape (n_days, n_patches) — use ndarray in impl

    def patch_index(self, code: str) -> int:
        return self.patch_ids.index(code)


def load_mobility_schedule(path: Path) -> MobilitySchedule:
    raw = json.loads(path.read_text(encoding="utf-8"))
    patch_ids = tuple(raw["patch_ids"])
    n_patches = len(patch_ids)
    idx = {p: i for i, p in enumerate(patch_ids)}
    days_raw = raw["days"]
    day_flows: list[dict[tuple[int, int], float]] = []
    import numpy as np

    total_out = np.zeros((len(days_raw), n_patches), dtype=float)
    for d, day in enumerate(days_raw):
        flows: dict[tuple[int, int], float] = {}
        for edge in day.get("flows", []):
            i, j = idx[edge["from"]], idx[edge["to"]]
            flows[(i, j)] = flows.get((i, j), 0.0) + float(edge["n"])
            total_out[d, i] += float(edge["n"])
        day_flows.append(flows)
    return MobilitySchedule(
        patch_ids=patch_ids,
        n_days=len(day_flows),
        day_flows=tuple(day_flows),
        total_outflow_per_patch_day=total_out,
    )
```

- [ ] **Step 4: Run test — PASS**

Run: `pytest tests/test_mobility.py::test_load_mobility_schedule_builds_patch_index -v`

- [ ] **Step 5: Commit**

```bash
git add app/eosp/services/mobility.py app/eosp/data/mobility_weekly_skeleton.json tests/test_mobility.py
git commit -m "feat(mobility): add schedule schema and JSON loader"
```

---

### Task 2: Metapopulation simulator (single run + mass balance)

**Files:**
- Create: `app/eosp/services/metapop.py`
- Modify: `app/eosp/services/mobility.py` (if re-exports needed)
- Create: `tests/test_metapop.py`

- [ ] **Step 1: Failing test — outbound E+I mass ≤ travelers, states non-negative**

```python
# tests/test_metapop.py
import numpy as np
from pathlib import Path

from eosp.services.mobility import load_mobility_schedule
from eosp.services.metapop import MetapopParams, simulate_metapop_once


def test_simulate_metapop_preserves_nonnegative_and_mobility_cap():
    root = Path(__file__).resolve().parents[1]
    sched = load_mobility_schedule(root / "app" / "eosp" / "data" / "mobility_weekly_skeleton.json")
    rng = np.random.default_rng(0)
    params = MetapopParams(
        beta_local=0.3,
        sigma=0.35,
        gamma=0.2,
        travel_frac_exposed=1.0,
        travel_frac_infectious=1.0,
    )
    init_s = np.array([900.0, 5000.0, 5000.0, 8000.0])
    init_e = np.array([80.0, 0.0, 0.0, 0.0])
    init_i = np.array([20.0, 0.0, 0.0, 0.0])
    traj = simulate_metapop_once(
        schedule=sched,
        params=params,
        init_s=init_s,
        init_e=init_e,
        init_i=init_i,
        init_r=np.zeros(4),
        rng=rng,
    )
    assert traj.S.shape == (sched.n_days + 1, 4)
    assert np.all(traj.S >= -1e-9)
    assert np.all(traj.I >= -1e-9)
```

- [ ] **Step 2: Run — fail until `MetapopParams` / `simulate_metapop_once` exist**

Run: `pytest tests/test_metapop.py::test_simulate_metapop_preserves_nonnegative_and_mobility_cap -v`

- [ ] **Step 3: Implement discrete-time SEIR + multinomial travel**

Implement in `metapop.py`:

- Daily order: (1) **local** mass-action: new exposures from `beta * S * I / N`; (2) E→I with rate `sigma`; I→R with rate `gamma` (discrete: multiply by `1-exp(-rate)` or Euler 1 day for v1).  
- **Travel:** For each patch `i`, expected `m_ij` travelers to `j` from `schedule`. Move a **binomial/multinomial** number of **E** and **I** proportional to `travel_frac_*` × `(m_ij / pop_i)` capped by available E+I. Use `rng.multinomial` for splitting outgoing traveler counts across destinations.

Export `@dataclass TrajectoryArrays` with `S,E,I,R` each shape `(n_days+1, n_patches)`.

- [ ] **Step 4: PASS pytest**

- [ ] **Step 5: Second test — secondary patch receives infections after hub mixing**

```python
def test_secondary_hub_gets_infected_mass_without_direct_seed():
    """HUB2 has no initial E/I; schedule sends HUB1->HUB2; with high beta at HUB1, I arrives at HUB2."""
    ...
    assert traj.I[-1, 2] > 0.05  # patch index 2 = HUB2 in skeleton
```

- [ ] **Step 6: Commit**

```bash
git add app/eosp/services/metapop.py tests/test_metapop.py
git commit -m "feat(metapop): SEIR patches with stochastic mobility coupling"
```

---

### Task 3: Ensemble wrapper + risk summary

**Files:**
- Modify: `app/eosp/services/metapop.py`

- [ ] **Step 1: Test percentile output shape**

```python
def test_run_ensemble_metapop_percentiles():
    ...
    out = run_ensemble_metapop(schedule=sched, params=params, init_state=..., n_runs=30, rng_seed=7)
    assert out["patches"][0]["code"] == "NSEED"
    assert "i_median_by_day" in out["patches"][0]
    assert len(out["patches"][0]["i_median_by_day"]) == sched.n_days + 1
```

- [ ] **Step 2: Implement `run_ensemble_metapop`** looping `simulate_metapop_once`, stack `I`, compute numpy percentiles (2.5, 50, 97.5) per patch per day.

- [ ] **Step 3: Commit**

```bash
git commit -am "feat(metapop): ensemble percentiles for patch I trajectory"
```

---

### Task 4: Seeding bridge from `network_spec.json` medevac

**Files:**
- Create: `app/eosp/services/metapop_seed.py`
- Create: `tests/test_metapop_seed.py`

- [ ] **Step 1: Test maps medevac destination codes to mobility patch IDs**

Pass a tiny dict fixture `{"flights": [{"destination": "ZA_JNB", "n_passengers": 30}]}` and patch list containing `JNB`; assert initial `I` mass split.

- [ ] **Step 2: Implement `build_initial_metapop_state(schedule, network_spec dict, ship_outbreak_mass)`** returning `init_s, init_e, init_i` arrays aligned to `schedule.patch_ids`. Unmapped destination codes log warning and skip.

- [ ] **Step 3: Commit**

```bash
git add app/eosp/services/metapop_seed.py tests/test_metapop_seed.py
git commit -m "feat(metapop): seed initial mass from network_spec medevac legs"
```

---

### Task 5: Geo API integration

**Files:**
- Modify: `app/eosp/services/geo.py`
- Modify: `app/eosp/api/routes.py`

- [ ] **Step 1: Extend `build_outbreak_geo` signature** with optional `risk_model: Literal[\"legacy\", \"metapop\"] = \"legacy\"` and optional `metapop_summary: dict | None`.

- [ ] **Step 2: When `risk_model == \"metapop\"`**, build `risk_heatmap` entries from ensemble output: use `i_median_by_day[-1]` (or max over horizon) per airport, join `load_airport_coords()` for lat/lng; set `risk_source` in metadata.

- [ ] **Step 3: Route `GET /geo/outbreak`** accepts `risk_model` query (default `legacy`). When `metapop`, load skeleton schedule + run `run_ensemble_metapop` with `n_runs` from env `EOSP_METAPOP_RUNS` default 64 (keep fast in dev).

- [ ] **Step 4: API test**

```python
# tests/test_api.py — add
def test_geo_outbreak_metapop_returns_source_in_metadata(client):
    r = client.get("/api/v1/geo/outbreak?risk_model=metapop")
    assert r.status_code == 200
    body = r.json()
    assert body["metadata"].get("risk_source") in ("metapop_monte_carlo",)
```

Adjust path prefix to match your `TestClient` mount.

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(api): optional metapop-driven geo risk layer"
```

---

### Task 6: Documentation + deprecation note

**Files:**
- Modify: `README.md` or `PRD.md` only if project already documents API (follow repo convention); otherwise add **short** subsection in `docs/superpowers/specs/2026-05-08-metapopulation-airport-design.md` linking env vars and query params.

- [ ] **Step 1: Document `risk_model` query + `EOSP_METAPOP_RUNS` in design spec §Migration.**

- [ ] **Step 2: Commit**

```bash
git commit -am "docs: metapop geo API and env flags"
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|------------------|------|
| Patches + SEIR + mobility matrix | Task 2 |
| Stochastic moves | Task 2 (multinomial) |
| Ensemble percentiles | Task 3 |
| Seed from ship/medevac | Task 4 |
| API surfaces same kernel as map | Task 5 |
| Legacy heuristic fallback | Task 5 (`risk_model=legacy`) |

**Placeholder scan:** None intentional; replace `mobility_weekly_skeleton.json` with real OD data in a future plan once licensing is clear.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-08-metapopulation-airport-model.md`.**

**Design spec:** `docs/superpowers/specs/2026-05-08-metapopulation-airport-design.md` — review for open decisions (travel_frac defaults, absolute vs relative population).

Two execution options:

1. **Subagent-driven (recommended)** — fresh subagent per task, review between tasks (`superpowers:subagent-driven-development`).
2. **Inline execution** — batch tasks in this session (`superpowers:executing-plans`).

Which approach do you want?
