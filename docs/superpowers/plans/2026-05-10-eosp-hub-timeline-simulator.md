# EOSP hub timeline simulator — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the retired ABM Monte Carlo path with an unbounded‑agent hub timeline simulator, produce `ForecastResponse` + `metadata.geo_forecast` compatible with the dashboard map and add a replay block for future time‑scrub UX.

**Architecture:** Isolate the stochastic kernel and aggregation in `eosp.services.hub_timeline` (subpackage): timeline anchoring → daily event loop (movement, compartment updates, weighted hub load → contact‑level transmission with negative binomial counts) → per‑trajectory daily bucket tallies → parallel ensemble over RNG streams → percentile merge into existing `ForecastPoint` + `geo_forecast.by_day` shape + sparse `replay_geo`. Wire `forecast.build_forecast` / `jobs.JobManager._run_full_refresh` to call into this package; keep inference unchanged.

**Tech stack:** Python 3.11+, NumPy (`numpy.random.Generator`: `poisson`, `negative_binomial` for NB2 contact counts). Optional SciPy only if future duration fitting needs it. Runtime requires `pip install -e ".[simulation,test]"` (NumPy ships with the `simulation` extra).

**Authoritative design:** [`docs/superpowers/specs/2026-05-10-eosp-hub-timeline-simulator-design.md`](../specs/2026-05-10-eosp-hub-timeline-simulator-design.md).

---

## File map (roles)

| Path | Responsibility |
|------|----------------|
| [`app/eosp/services/hub_timeline/__init__.py`](../../../../app/eosp/services/hub_timeline/__init__.py) | Re-export `run_hub_timeline_forecast`, `HubTimelineSimulatorConfig`, `simulate_trajectory` |
| [`app/eosp/services/hub_timeline/config.py`](../../../../app/eosp/services/hub_timeline/config.py) | `HubTimelineSimulatorConfig` dataclass: contact means (`mu_travel`, `mu_stay`, `mu_onset_burst`), NB dispersion `r_nb`, load scale `alpha_load`, state weights (`k_presympt`, `eps_hosp`), mobility probabilities, durations scales, CFR, Horizon 30 flag, cohort policy |
| [`app/eosp/services/hub_timeline/timeline.py`](../../../../app/eosp/services/hub_timeline/timeline.py) | `simulation_dates(cases, index_case_id)`, `eligible_cases()`, `ObservationKind.individual` filter |
| [`app/eosp/services/hub_timeline/routes_graph.py`](../../../../app/eosp/services/hub_timeline/routes_graph.py) | Thin wrapper: `parse_route_edge_counts`, `outbound_weights_from_counts`, aggregate destination weights for **home sampling** (`3/4` branch) |
| [`app/eosp/services/hub_timeline/contacts.py`](../../../../app/eosp/services/hub_timeline/contacts.py) | `draw_contact_count(rng, mu, r_nb)`, `prob_infection(p_transmit, h2h, load_eff, baseline_scale=1.0)` with `load_eff = 1 + cfg.alpha_load * max(0, L)` |
| [`app/eosp/services/hub_timeline/agents.py`](../../../../app/eosp/services/hub_timeline/agents.py) | `@dataclass` `Agent`: `kind` synthetic|fixed, `state`, `iata_current`, `iata_home`, `mob_phase` (home_only|away_must_return), counters for E/P/I/H durations, `symptom_onset_date_ref` optional for fixed agents |
| [`app/eosp/services/hub_timeline/kernel.py`](../../../../app/eosp/services/hub_timeline/kernel.py) | `simulate_trajectory(...) -> HubTrajectoryDataclass`: daily logs `L_by_hub`, cumulative infected stock by hub (+ by day snapshots), infectious weight for **A**, global tallies |
| [`app/eosp/services/hub_timeline/ensemble.py`](../../../../app/eosp/services/hub_timeline/ensemble.py) | `run_ensemble(cases, inference, scenario_overrides, n_simulations, seed)`: sample `p_transmit`, `h2h_multiplier`, `cfr` like legacy intent (truncate from `InferenceResult.parameters` summaries), spawn threads or `numpy` vectorization avoidance—use **`concurrent.futures`** with chunked workers; aggregate into `ForecastResponse` |
| [`app/eosp/services/hub_timeline/geo_output.py`](../../../../app/eosp/services/hub_timeline/geo_output.py) | Build **`geo_forecast`** dict matching [`tests/test_geo_abm_heatmap.py`](../../../../tests/test_geo_abm_heatmap.py) expectation: `{ "metadata": {...}, "by_day": [...] }` with `bucket` keys **`f"{country}_{iata}"`** using `case_record.location_country`/airport fallback from `routes_graph`/`load_iata_to_country` |
| [`app/eosp/services/hub_timeline/replay.py`](../../../../app/eosp/services/hub_timeline/replay.py) | From stacked trajectory samples, compose sparse `replay_geo`: list of `{ "date": "...", "airports": { "JFK": {"A_median":..., "C_median":...} } }`; omit airports where **both** medians zero that day |
| [`app/eosp/services/forecast.py`](../../../../app/eosp/services/forecast.py) | Replace `SimulatorRemovedError` in `build_forecast` with call to ensemble; keep `ForecastNotCachedError` contract |
| [`app/eosp/services/jobs.py`](../../../../app/eosp/services/jobs.py) | After inference: run baseline + scenario list from `_ensemble_config` / settings; `cache_forecast` each |
| [`app/eosp/core/settings.py`](../../../../app/eosp/core/settings.py) | **`eosp_hub_index_case_id`** (optional `UUID` string)—first matched case skipped for anchor+bursts |
| [`.env.example`](../../../../.env.example) | Document `EOSP_HUB_INDEX_CASE_ID` |

---

### Task 1: Timeline + eligibility (pure helpers + tests)

**Files:**
- Create: [`app/eosp/services/hub_timeline/timeline.py`](../../../../app/eosp/services/hub_timeline/timeline.py)
- Create: [`app/eosp/services/hub_timeline/__init__.py`](../../../../app/eosp/services/hub_timeline/__init__.py) (empty stub)
- Create: [`tests/test_hub_timeline_timeline.py`](../../../../tests/test_hub_timeline_timeline.py)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hub_timeline_timeline.py
from datetime import date
from uuid import UUID, uuid4

from eosp.core.models import CaseRecord, CaseStatus, LabResult, ObservationKind
from eosp.core.seed_data import CASES
from eosp.services.hub_timeline.timeline import compute_simulation_calendar, eligible_individual_cases


def _individual(
    case_id: UUID,
    onset: date,
    airport: str | None,
    patient: str = "p",
) -> CaseRecord:
    base = CASES[0]
    return CaseRecord(
        case_id=case_id,
        patient_identifier=patient,
        symptom_onset_date=onset,
        location_country="ZA",
        location_airport_code=airport,
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.NOT_TESTED,
        data_source="test",
        ingestion_timestamp=base.ingestion_timestamp,
        record_updated_at=base.record_updated_at,
        validation_score=0.9,
        observation_kind=ObservationKind.INDIVIDUAL,
    )


def test_anchor_skips_index_case_for_anchor_only():
    index_id = uuid4()
    cases = [
        _individual(index_id, date(2026, 5, 1), "JNB", "index"),
        _individual(uuid4(), date(2026, 5, 3), "JNB", "a"),
        _individual(uuid4(), date(2026, 5, 5), "CPT", "b"),
    ]
    allowed = frozenset({"JNB", "CPT"})
    elig = eligible_individual_cases(cases, allowed_iatas=allowed, index_case_id=index_id)
    anchor, days = compute_simulation_calendar(elig, horizon_days=30)
    assert anchor == date(2026, 5, 3)
    assert len(days) == 30
    assert days[0] == anchor
    assert days[-1] == date(2026, 6, 1)


def test_eligible_drops_unknown_airport():
    e = eligible_individual_cases(
        [_individual(uuid4(), date(2026, 5, 1), "XXX")],
        allowed_iatas=frozenset({"JNB"}),
        index_case_id=None,
    )
    assert e == []
```

Run: `pytest tests/test_hub_timeline_timeline.py -v` — expect **`ModuleNotFoundError` / ImportError`** until `timeline.py` exists, then assertion failures until implemented.

- [ ] **Step 2: Implement `compute_simulation_calendar`**

Signatures:

```python
def eligible_individual_cases(
    cases: list[CaseRecord],
    *,
    allowed_iatas: frozenset[str],
    index_case_id: object | None = None,
) -> list[CaseRecord]:
    ...

def compute_simulation_calendar(
    eligible: list[CaseRecord],
    *,
    horizon_days: int = 30,
) -> tuple[date, list[date]]:
    """Returns (anchor_date, day_by_day calendar list length horizon_days).
    Anchor = min(symptom_onset_date).
    """
    ...
```

- [ ] **Step 3: Re-run pytest** → PASS

- [ ] **Step 4: Commit** `feat(hub-timeline): add simulation calendar helpers`

---

### Task 2: Settings + env for index-case exclusion

**Files:**
- Modify: [`app/eosp/core/settings.py`](../../../../app/eosp/core/settings.py)
- Modify: [`.env.example`](../../../../.env.example)

- [ ] Add optional `EOSP_HUB_INDEX_CASE_ID: str | None = None`; parse UUID in bootstrap path or let `timeline` accept `str` and compare `str(case.case_id)`.

- [ ] Document in `.env.example`.

- [ ] **Test:** `tests/test_hub_timeline_settings.py` optional—smoke‑read Settings with env override mocked.

- [ ] Commit `config: optional EOSP_HUB_INDEX_CASE_ID for hub timeline`

---

### Task 3: Contact draws + infection probability

**Files:**
- Create: [`app/eosp/services/hub_timeline/contacts.py`](../../../../app/eosp/services/hub_timeline/contacts.py)
- Create: [`tests/test_hub_timeline_contacts.py`](../../../../tests/test_hub_timeline_contacts.py)

- [ ] **Implement `draw_contact_count`**

Use **`numpy.random.Generator.negative_binomial`** (available under `.[simulation]`) for NB2 with mean \(\mu\) and shape \(r>0\): parameterization **`nfailures = r`**, **`p = r / (r + mu)`** so \(\mathbb{E}[X] = r \frac{1-p}{p} = \mu\).

```python
def draw_contact_count(rng, mu: float, r_disp: float) -> int:
    if r_disp >= 1e9 or mu <= 0:
        return int(rng.poisson(max(mu, 0.0)))
    p = r_disp / (r_disp + mu)
    return int(rng.negative_binomial(r_disp, p))
```

- [ ] **`transmission_probability(p_transmit: float, h2h: float, load_at_hub: float, *, alpha_load: float) -> float`**

```python
import math

def transmission_probability(p_transmit, h2h, load_at_hub, *, alpha_load):
    eff = 1.0 + alpha_load * max(0.0, load_at_hub)
    # Simple hazard-style cap
    return min(1.0 - math.exp(-p_transmit * h2h * eff), 0.999999)
```

- [ ] **Test:** fixed seed, `mu=20`, `r_disp=10` — mean of 5000 draws ≈ 20 ± tolerance; `r_disp` huge matches Poisson.

- [ ] Commit `feat(hub-timeline): contact draws and load-scaled transmission`

---

### Task 4: Route graph + home sampling

**Files:**
- Create: [`app/eosp/services/hub_timeline/routes_graph.py`](../../../../app/eosp/services/hub_timeline/routes_graph.py)
- Create: [`tests/test_hub_timeline_routes_graph.py`](../../../../tests/test_hub_timeline_routes_graph.py)

- [ ] Load paths from same location as OpenFlights bundle used elsewhere (grep `routes.dat` in repo for `Path`).

- [ ] Expose `build_world_graph(routes_path, airports_path) -> tuple[frozenset[str], dict[str, dict[str, int]], dict[str, str]]` (allowed iatas, outbound weights, iata→country).

- [ ] **`sample_home_airport(rng, current_iata, outbound, allowed, p_home_here=0.25)`** — with prob 0.25 return `current_iata`; else sample destination weighted by edge multiplicities **pooling all edges from any origin** or **from current** per spec: “3/4 from airports listed in routes” — use **global destination frequency** from full `edge_counts` keys `(a,b)` weighting `b` (document this design choice in module docstring: *non-local home prior*).

- [ ] Test: duplicate edges increase probability mass.

- [ ] Commit `feat(hub-timeline): OpenFlights route weights and home sampling`

---

### Task 5: Agent dataclass + mobility step (synthetic)

**Files:**
- Create: [`app/eosp/services/hub_timeline/agents.py`](../../../../app/eosp/services/hub_timeline/agents.py)
- Create: [`tests/test_hub_timeline_mobility.py`](../../../../tests/test_hub_timeline_mobility.py)

- [ ] Define `AgentKind` enum `SYNTHETIC`, `FIXED`.

- [ ] Define `AgentState` `E`, `P`, `I`, `H`, `R`, `D` as `IntEnum` matching legacy codes if useful for debugging.

- [ ] Synthetic mobility: **`step_mobility_synthetic(agent, rng, outbound, cfg)`**:

  Rules (v1 implementable):

  - If **`iata_current == iata_home`**: each day **`p_stay`** (default 0.55) remain; else pick random outbound flight from hub weighted by **`outbound[home]`** to visit **single** foreign hub (**set phase `away`**).
  - If **`phase == away`**: **`p_return`** (default 0.65) set `current = home`; else stay at foreign hub. **Never** initiate a move to third airport while away.

- [ ] Tests: RNG stub shows **no triangular routing** (`away` ⇒ only `stay` at foreign or `return`).

- [ ] Commit `feat(hub-timeline): synthetic airport mobility states`

---

### Task 6: Disease progression primitives (gamma / weibull draws)

**Files:**
- Create: [`app/eosp/services/hub_timeline/disease_clock.py`](../../../../app/eosp/services/hub_timeline/disease_clock.py)
- Modify: [`app/eosp/services/hub_timeline/config.py`](../../../../app/eosp/services/hub_timeline/config.py)

- [ ] **`sample_e_duration`** / **`sample_p_duration`** from gamma matching retirement doc intuition (reuse constants from archived knowledge in comments or **`InferenceResult`** `incubation` mean ± factor).

- [ ] **`sample_i_duration`** Weibull 1.5 × scale drawn from inferred `parameters` summaries when present **else defaults** `{ "mean": 7.0, "std": 2.0 }` mapped roughly to scale/shape externally.

For v1 correctness over parity: **`eosp.services.inference`** does not expose old kernel code—implement **minimal** tables in `HubTimelineSimulatorConfig(latent_gamma_shape=..., ...)` editable from **`scenarios.json`** `hub_timeline` block newly added parallel to `parameter_overrides`.

Add to **each scenario** optional JSON subtree:

```json
"hub_timeline": {
  "contacts": { "mu_travel": 20, "mu_stay": 5, "mu_onset_burst": 20, "r_dispersion": 8.0 },
  "load": { "alpha": 0.08 },
  "mobility": { "p_stay_home": 0.55, "p_return_when_away": 0.65 }
}
```

- [ ] **Test:** draws positive integers only.

- [ ] Commit `feat(hub-timeline): configurable disease durations`

---

### Task 7: Fixed (observed) agent lifecycle (**Option B**)

**Logic (encode in [`kernel.py`](../../../../app/eosp/services/hub_timeline/kernel.py) section or `agents_fixed.py`):**

1. **Before** calendar date `< symptom_onset_date` — agent **inactive** (`state` hidden or excluded from **`L`** accumulation and no contacts emanate).
2. **On onset date:** enter **`I`** at `location_airport_code` (**no P phase** per Option B shortcut for **fixed**, still documented). Fire **burst contacts** separately (caller loop).
3. While **`I`** and **`d < hospitalization_date`** (if any): pinned; daily contact draws `mu_stay` while pathogenically transmitting (reuse **stay** semantics).
4. On **`hospitalization_date`** if set: **`H`**; durations sample Weibull; exit `H → R` unless `death_date` sooner.
5. **`death_date`**: force **`D`** on that date overriding other transitions.

6. **`death_date` is None`** after **`I`** ends (never hospitalized):

   - Infectious dwell for `Ti` days sampled at onset; then **`Bernoulli(cfr)`** for `R`/`D`; **no onward movement** afterward.

Implement **inactive** explicitly by **`state is None`** or `active_from date` comparison.

**Test:** simulate one fixed agent timeline only with mock kernel slice—assert **`L`** on day onset-1 equals 0 contribution from that agent; on onset **`L` > 0** if weighting nonzero.

Commit `feat(hub-timeline): fixed observed lifecycle (Option B)`

---

### Task 8: **`simulate_trajectory` kernel**

**Files:**
- Create: [`app/eosp/services/hub_timeline/kernel.py`](../../../../app/eosp/services/hub_timeline/kernel.py)
- Create: [`tests/test_hub_timeline_kernel_smoke.py`](../../../../tests/test_hub_timeline_kernel_smoke.py)

Pseudocode (implement literally):

```python
def simulate_trajectory(
    cases: list[CaseRecord],
    *,
    inference_params: dict[str, tuple[float,float]],
    world: WorldGraphBundle,
    config: HubTimelineSimulatorConfig,
    rng: numpy.random.Generator,
) -> HubTrajectorySnapshot:
```

**Daily:**

1. **Activate** newly onset fixed agents (`date == onset`).
2. **Decrement clocks** synthetic E/P/I/H transitions; births from `E→P→I`; handle terminations **`I→{H,R,D}`**, **`H→{R,D}`**.
3. **Compute infectious load** **`L_eff[iata]`**: sum **`state_weight(agent.state)` × (agent transmitting flag)`** excluding inactive fixed agents and excluding **`E`**, **`R`**, **`D`** synthetic.
   - Synthetic **presympt**: weight `cfg.k_presympt`.
   - **H**: **`cfg.eps_hosp`**.

4. **`new_arrivals` flag synthetic:** `prev_airport snapshot` comparison per agent to detect **`arrival`** — if **`iata`** changed versus yesterday, **`mu_travel`** else **`mu_stay`** (**fixed pinned** ⇒ always **stay**, except **inactive** ⇒ no contacts).

5. **Transmission:** iterate each generator agent (Synthetic `I+P` weights + Fixed `I+H` microscopic—**omit H if eps ~0`): draw **`n = draw_contact_count(rng,...)`**. For **`k`** in **`1..n`**: draw **potential infectee hub = current hub** (*same airport abstraction* post-flight lumping). Decide success with **`transmission_probability`**. On success **`spawn_synthetic`** in **`E`** with clocks drawn from **`disease_clock`**.

   **Burst:** iterate **eligible fixed** cases **`date == symptom_onset`**: draw **`nburst`** at `location_airport_code` (**mean `mu_onset_burst`**).

6. **Synthetic mobility:** after transmissions for the calendar day, apply **`step_mobility_synthetic`** (skip agents who are **R**, **D**, **H** with no travel, or **fixed** pinned).

7. **Record daily stats:** **`cumulative_infected_by_airport`** (first time **`E`** recorded + fixed count at onset? — **Recommended:** **cumulative incidence at hub**: count agents whose **initial acquisition** originated at hub **optional** harder—**v1 tally:** **`C` = cumulative count of `{synthetic+P+I+H+R+D}` attributed to **`current_airport`** end-of-day ambiguous—instead **match geo retirement intent:** **`cumulative infected stock present at hub`** end-of-day: count agents **`state ∉ {S dormant}` whose `iata_current == hub`** (**Fixed** count when active). Synthetic **travel** reallocates attribution daily.

Store arrays: `cum_inf[day_idx, hub]`, **`infectious_weight_A[day_idx, hub]` = sum weights for **`P+I`** (**fixed I only when active**) — **omit H** microscopic from **A definition** optionally—spec `"P+I weighted"` ⇒ **implement that**.

**Smoke test:** **`n_days=3`**, `p_transmit=0.0` ⇒ no secondary synthetic from seed—only fixed **bursts** still create attempts but fail—**assert** secondary count 0.

**Smoke test:** **`p_transmit=0.99`**, `mu_stay=0`, single hub tiny world **assert** growth monotonic.

Commit `feat(hub-timeline): daily kernel with load and bursting`

---

### Task 9: **`geo_output` builder + tests against existing heatmap helper**

**Files:**
- Create: [`app/eosp/services/hub_timeline/geo_output.py`](../../../../app/eosp/services/hub_timeline/geo_output.py)
- Modify: [`tests/test_geo_abm_heatmap.py`](../../../../tests/test_geo_abm_heatmap.py) or new file importing same helper

- [ ] Take list of per-traj `by_day` dicts—stack `cumulative_infected` & `infectious_I` medians per bucket.

- [ ] **Bucket key** `f"{country}_{iata}"` with country from `iata_to_country`.

- [ ] **Assert** `_risk_heatmap_rows_from_abm_geo_forecast` still returns final-day **C** median.

Commit `feat(hub-timeline): geo_forecast metadata builder`

---

### Task 10: **`replay_geo` sparse builder**

**Files:**
- Create: [`app/eosp/services/hub_timeline/replay.py`](../../../../app/eosp/services/hub_timeline/replay.py)
- Create: [`tests/test_hub_timeline_replay.py`](../../../../tests/test_hub_timeline_replay.py)

- [ ] Input: `list[HubTrajectorySnapshot]` length `N`, output JSON per spec §10 with schema_version **`eosp_geo_replay_1`**.

- [ ] For each day & airport: compute **median** `A` and **median** `C`; **drop** if both `1e-12` abs threshold zero.

- [ ] Test: synthetic constant trajectories → medians equal raw.

Commit `feat(hub-timeline): sparse replay median surfaces`

---

### Task 11: **`ensemble.py` + `ForecastResponse`**

**Files:**
- Create: [`app/eosp/services/hub_timeline/ensemble.py`](../../../../app/eosp/services/hub_timeline/ensemble.py)
- Create: [`tests/test_hub_timeline_ensemble.py`](../../../../tests/test_hub_timeline_ensemble.py)

- [ ] **`build_forecast_response(scenario_name, traj_percentiles_daywise) -> ForecastResponse`**

Rebuild [`ForecastPoint`](../../../../app/eosp/core/models.py): `cases_cumulative` keys `"median"` `"p2_5"` etc. mimic existing consumer in [`forecast.compare_scenarios`](../../../../app/eosp/services/forecast.py).

- [ ] **`metadata` keys:** **`engine: "hub_timeline_monte_carlo"`**, `ensemble_spec_hash` sha256 digest of **`scenario+"|"+str(n)+"..."`**, **`geo_forecast`**, **`replay_geo`** (optional null if **`n_sim`** small?), **`scenario_hub_timeline` merged snapshot**.

- [ ] **`n_workers`:** min( os.cpu_count() or 4, 16 ) chunked.

Commit `feat(hub-timeline): ensemble aggregation to ForecastResponse`

---

### Task 12: **`forecast.py` wiring**

**Files:**
- Modify: [`app/eosp/services/forecast.py`](../../../../app/eosp/services/forecast.py)

- [ ] **`build_forecast`** calls `ensemble.run_hub_timeline_ensemble(...)`.

- [ ] Preserve `_FOREST_CACHE` pattern optional—invalidate on **new inference version**.

- [ ] **`run_forecast_engine`** returns **`ForecastRunItem`** with execution wall time measured.

Commit `feat(forecast): restore build_forecast via hub timeline simulator`

---

### Task 13: **`jobs.py` simulation stage**

**Files:**
- Modify: [`app/eosp/services/jobs.py`](../../../../app/eosp/services/jobs.py)

- [ ] Replace `simulator: "removed"` event with looping scenarios from **`scenarios.json`** names same as `_DEFAULT_BOOTSTRAP_SCENARIOS`: `baseline`, `terminal_distancing`, etc.

Map legacy **`network_modifications.modify_weights.itinerary_patch`** to **`hub_timeline.load.alpha *= itinerary_patch`** (initial mapping table in plan comment—implement linear scaling **0.62×** multiplier on **`alpha_load`** baseline).

 Map **`contacts_daily`** override — scale **`mu_*` proportional **(baseline `contacts_daily` estimated `4`** from inference param if absent else constant).

 Map **`p_transmit_scale`** — multiply **`p_transmit` draw.**

- [ ] **`record.detail["simulator"] = "hub_timeline"`**

Commit `feat(jobs): run hub timeline ensembles after inference`

---

### Task 14: API + **`routes.py`** error path sanity

**Files:**
- Modify: [`app/eosp/api/routes.py`](../../../../app/eosp/api/routes.py)

- [ ] **`POST /forecasts/run`** should succeed end-to-end in integration smoke (optional **`pytest`** `TestClient`), keep catching **`SimulatorRemovedError`** renamed—remove dead branch if unreachable.

Commit `fix(api): align forecast run handler with revitalized simulator`

---

### Task 15: Documentation hygiene

**Files:**
- Modify: [`docs/ABM_RETIREMENT.md`](../../../ABM_RETIREMENT.md)
- Modify: [`app/eosp/data/scenarios.json`](../../../../app/eosp/data/scenarios.json)

- [ ] Add link from retirement doc §6 to **`2026-05-10` spec** noting **replacement shipped**.

- [ ] Append minimal **`hub_timeline` stubs** `{}` acceptable defaults for each scenario to avoid **`KeyError`**.

Commit `docs: link hub timeline spec and scenarios defaults`

---

## Spec coverage checklist (plan author self-review)

| Spec § | Satisfied by task |
|--------|-------------------|
| Timeline anchor §3 | Task 1, 2 |
| Eligibility / skip index | Tasks 1, 2 |
| Synthetic full E+P+I+H+R+D | Tasks 6, 8 |
| Fixed agents Option B §6.2 §3.4 | Task 7, 8 |
| Contacts NB §7 | Task 3, 8 |
| Load coupling §7.3 | Task 3 (trans_prob), Task 8 |
| Onset burst §7.4 | Task 8 |
| Outputs / tallies §8 | Tasks 8, 11 |
| Geo C-layer §9 | Task 9 |
| Replay §10 | Task 10 |
| Integration §11 | Tasks 12–15 |

---

**Plan complete and saved to [`docs/superpowers/plans/2026-05-10-eosp-hub-timeline-simulator.md`](2026-05-10-eosp-hub-timeline-simulator.md). Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration (**subagent-driven-development** skill).

2. **Inline Execution** — execute tasks in this session using **executing-plans** checkpoints.

**Which approach do you want?**
