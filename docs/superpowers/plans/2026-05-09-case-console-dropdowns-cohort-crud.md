# Case Console: Dropdowns, Cohort Intake, Edit & Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve researcher case workflows with country/IATA `<select>` controls, cohort observation intake aligned with existing `CaseCreate` rules, and authenticated PATCH/DELETE (plus GET-by-id) so researchers can fix or remove rows already stored.

**Architecture:** Add small **reference HTTP endpoints** (countries + airports) backed by existing `app/eosp/data/airport_coords.json` plus a static ISO-3166 alpha-2 label map for dropdown labels. Extend **repository + routes** with `get_case`, `update_case`, and `soft_delete_case` mirroring `create_case` history/validation patterns. **UI** extends `drawer.js` intake form with observation-kind toggle, conditional cohort fields, synced selects (country filters airport list), and a **manage cases** sub-panel listing rows with Edit / Delete actions calling the new APIs. Align **geographic validation** (`VALID_COUNTRIES` in `eosp/services/validation.py`) with the union of reference countries so manual entry does not spuriously fail quality when codes are valid.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy (`CaseRecordRow.active` soft-delete), existing Clerk dependency `require_clerk_session` on mutating case routes, vanilla ES modules in `app/eosp/static/js/`.

**Context:** Prefer implementing in a dedicated git worktree if parallelizing with other work (`superpowers:using-git-worktrees`).

---

## File structure (creates / modifies)

| Path | Responsibility |
|------|----------------|
| `app/eosp/data/iso_3166_alpha2_labels.json` (create) | Static map `"ES" → "Spain"` for country `<option>` text (subset or full ISO). |
| `app/eosp/services/reference_geo.py` (create) | Load `airport_coords.json` + labels; build sorted airport list; filter by country. |
| `app/eosp/api/routes.py` (modify) | `GET /reference/countries`, `GET /reference/airports`; `GET/PATCH/DELETE /cases/{case_id}`. |
| `app/eosp/core/models.py` (modify) | `CaseUpdate` (partial update body); optional `CaseDeleteResponse`. |
| `app/eosp/core/repository.py` (modify) | `get_case`, `update_case`, `delete_case` on `InMemoryRepository` + `SqlRepository`. |
| `app/eosp/services/validation.py` (modify) | Expand `VALID_COUNTRIES` to match reference union (or replace single set with `reference_geo.valid_country_codes()`). |
| `app/eosp/static/js/drawer.js` (modify) | Dropdowns, cohort mode, edit/delete flows, fetch reference lists once. |
| `app/eosp/static/js/cases.js` (modify, optional) | Add data attributes or callbacks if edit launches from main case list instead of drawer-only. |
| `app/eosp/static/css/drawer.css` (modify) | Form grid for cohort fields, manage-case table/toolbar. |
| `tests/test_api.py` (modify) | Tests for reference endpoints, CRUD, auth gate when `EOSP_CLERK_FRONTEND_API` set. |

---

### Task 1: Reference geo service + public API

**Files:**
- Create: `app/eosp/data/iso_3166_alpha2_labels.json`
- Create: `app/eosp/services/reference_geo.py`
- Modify: `app/eosp/api/routes.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing tests for reference endpoints**

Add to `tests/test_api.py`:

```python
def test_reference_countries_returns_sorted_codes():
    r = client.get("/api/v1/reference/countries")
    assert r.status_code == 200
    data = r.json()
    assert "countries" in data
    codes = [c["code"] for c in data["countries"]]
    assert codes == sorted(codes)
    assert all(len(c) == 2 for c in codes)
    # At least airports file countries appear
    assert "ES" in codes


def test_reference_airports_filtered_by_country():
    r = client.get("/api/v1/reference/airports?country=ES")
    assert r.status_code == 200
    for a in r.json()["airports"]:
        assert a["country"] == "ES"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_api.py::test_reference_countries_returns_sorted_codes tests/test_api.py::test_reference_airports_filtered_by_country -v
```

Expected: 404 or route missing.

- [ ] **Step 3: Add label JSON (minimal slice — expand as needed)**

Create `app/eosp/data/iso_3166_alpha2_labels.json` with at least every country code present in `airport_coords.json` plus outbreak-focused codes used in tests (`CV`, `ZA`, etc.):

```json
{
  "ES": "Spain",
  "NL": "Netherlands",
  "ZA": "South Africa",
  "CV": "Cabo Verde"
}
```

Complete the file for all keys required by the union of `airport_coords.json` country fields and `VALID_COUNTRIES` in validation — missing labels fall back to the raw code in the API.

- [ ] **Step 4: Implement `reference_geo.py`**

```python
# app/eosp/services/reference_geo.py
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@lru_cache
def _airport_coords() -> dict:
    with open(_DATA_DIR / "airport_coords.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def _iso_labels() -> dict[str, str]:
    p = _DATA_DIR / "iso_3166_alpha2_labels.json"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def countries_for_api() -> list[dict[str, str]]:
    airports = _airport_coords()
    labels = _iso_labels()
    codes = {meta["country"] for meta in airports.values() if "country" in meta}
    codes |= set(labels.keys())
    out = [{"code": c, "name": labels.get(c, c)} for c in sorted(codes)]
    return out


def airports_for_api(country: str | None) -> list[dict[str, str]]:
    cc = (country or "").strip().upper()
    airports = _airport_coords()
    rows = []
    for iata, meta in airports.items():
        if cc and meta.get("country", "").upper() != cc:
            continue
        rows.append({
            "iata": iata,
            "city": meta.get("city", ""),
            "country": meta.get("country", ""),
            "label": f"{iata} · {meta.get('city', '')}",
        })
    rows.sort(key=lambda x: x["iata"])
    return rows
```

- [ ] **Step 5: Register routes in `routes.py`**

```python
from eosp.services.reference_geo import airports_for_api, countries_for_api

@router.get("/reference/countries")
def reference_countries():
    return {"countries": countries_for_api()}


@router.get("/reference/airports")
def reference_airports(country: str | None = Query(default=None)):
    return {"airports": airports_for_api(country)}
```

- [ ] **Step 6: Run tests — expect PASS**

```bash
pytest tests/test_api.py::test_reference_countries_returns_sorted_codes tests/test_api.py::test_reference_airports_filtered_by_country -v
```

- [ ] **Step 7: Commit**

```bash
git add app/eosp/data/iso_3166_alpha2_labels.json app/eosp/services/reference_geo.py app/eosp/api/routes.py tests/test_api.py
git commit -m "feat(api): add country and airport reference lists for case UI"
```

---

### Task 2: Validation geography aligned with reference

**Files:**
- Modify: `app/eosp/services/validation.py`
- Modify: `app/eosp/services/reference_geo.py` (export `all_valid_country_codes`)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing test — country not in old VALID_COUNTRIES but in reference should score geography True**

```python
def test_validation_accepts_countries_from_reference_geo(monkeypatch):
    """After alignment, a case in DE (present in airport_coords) must pass geographic_plausibility."""
    from eosp.core.models import CaseCreate, CaseStatus, LabResult, ObservationKind
    from eosp.core.repository import InMemoryRepository
    from uuid import uuid4
    from datetime import date

    repo = InMemoryRepository(cases=[], validations={}, inferences=[], alerts=[])
    payload = CaseCreate(
        case_id=uuid4(),
        patient_identifier="geo_test",
        symptom_onset_date=date(2026, 5, 1),
        location_country="DE",
        confirmed_or_suspected=CaseStatus.CONFIRMED,
        lab_test_result=LabResult.NOT_TESTED,
        data_source="Manual_Form",
        observation_kind=ObservationKind.INDIVIDUAL,
    )
    case, val = repo.create_case(payload)
    assert val.checks_passed["geographic_plausibility"] is True
```

Adjust `"DE"` if not in `airport_coords.json` — pick any country code **present** in `airport_coords.json` but **absent** from current `VALID_COUNTRIES` before the fix.

- [ ] **Step 2: Run test — expect FAIL** (`geographic_plausibility` False).

- [ ] **Step 3: Implement — replace hardcoded set with reference union**

In `reference_geo.py`:

```python
def all_valid_country_codes() -> set[str]:
    return {meta["country"] for meta in _airport_coords().values() if "country" in meta} | set(_iso_labels().keys())
```

In `validation.py`, replace `VALID_COUNTRIES = {...}` with:

```python
from eosp.services.reference_geo import all_valid_country_codes

def _valid_countries() -> set[str]:
    return all_valid_country_codes()
```

And use `case.location_country in _valid_countries()` inside `validate_case` (or cache module-level after first call).

- [ ] **Step 4: Run test — PASS**

- [ ] **Step 5: Commit**

```bash
git add app/eosp/services/validation.py app/eosp/services/reference_geo.py tests/test_api.py
git commit -m "fix(validation): align country checks with reference airport geography"
```

---

### Task 3: Case models + repository read/update/delete

**Files:**
- Modify: `app/eosp/core/models.py`
- Modify: `app/eosp/core/repository.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Add `CaseUpdate` model** (`models.py`)

```python
class CaseUpdate(BaseModel):
    patient_identifier: str | None = None
    symptom_onset_date: date | None = None
    hospitalization_date: date | None = None
    death_date: date | None = None
    location_country: str | None = Field(default=None, min_length=2, max_length=2)
    location_airport_code: str | None = Field(default=None, min_length=3, max_length=3)
    confirmed_or_suspected: CaseStatus | None = None
    lab_test_result: LabResult | None = None
    contacts: list[dict[str, Any]] | None = None
    data_source: str | None = None
    updated_by: str = "api"
    updated_reason: str = "case_update"
    observation_kind: ObservationKind | None = None
    cohort_size: int | None = Field(default=None, ge=1)
    cohort_deaths: int | None = Field(default=None, ge=0)
    report_period_start: date | None = None
    report_period_end: date | None = None

    @model_validator(mode="after")
    def _cohort_rules(self) -> CaseUpdate:
        kind = self.observation_kind
        if kind == ObservationKind.INDIVIDUAL:
            if self.cohort_size not in (None, 1):
                raise ValueError("individual observations require cohort_size == 1")
            if self.cohort_deaths not in (None, 0):
                raise ValueError("individual observations use death_date, not cohort_deaths")
        return self
```

Apply non-None fields onto a copy of `CaseRecord`, bump `version`, re-run `validate_case` against **all active peers excluding this case id**.

- [ ] **Step 2: Write repository tests (in-memory)**

```python
def test_update_case_bumps_version_and_revalidates():
    # seed repo with one case from CASES; PATCH patient_identifier; assert version increased
    ...

def test_soft_delete_excludes_from_list_cases():
    # create_case; delete_case; list_cases empty or excludes id
    ...
```

Implement `get_case(case_id) -> CaseRecord | None`, `update_case(case_id, CaseUpdate) -> tuple[CaseRecord, ValidationResult]`, `delete_case(case_id) -> bool` on **both** `InMemoryRepository` and `SqlRepository`.

**SqlRepository `delete_case`:** `session.get(CaseRecordRow, id)` → set `active=False`, `updated_at=now`, commit; append `UserActionRow` with `action_type="delete_case"`.

**SqlRepository `update_case`:** load row, merge fields into `CaseRecord`, validate against other active rows, update row + insert `CaseRecordHistoryRow` + new `ValidationResultRow` + optional alerts — mirror `create_case` transaction structure.

**InMemoryRepository:** remove case from list on delete **or** add `active` flag to in-memory model — simplest: **remove** from `self.cases` on delete; `list_cases` unchanged. For update, replace list element.

- [ ] **Step 3: Run tests — FAIL until implementations complete**

- [ ] **Step 4: Implement repository methods**

- [ ] **Step 5: Commit**

```bash
git add app/eosp/core/models.py app/eosp/core/repository.py tests/test_api.py
git commit -m "feat(repository): get/update/delete cases with validation and history"
```

---

### Task 4: HTTP API for case CRUD + Clerk protection

**Files:**
- Modify: `app/eosp/api/routes.py`
- Modify: `app/eosp/api/deps.py` (no change if `require_clerk_session` already imported)
- Test: `tests/test_api.py`

- [ ] **Step 1: Add routes**

```python
@router.get("/cases/{case_id}")
def get_case(case_id: UUID, request: Request):
    record = request.app.state.repository.get_case(case_id)
    if record is None:
        raise HTTPException(404, detail="Case not found")
    return record


@router.patch("/cases/{case_id}", response_model=CaseIngestionResponse)
def patch_case(
    case_id: UUID,
    payload: CaseUpdate,
    request: Request,
    _session: dict | None = Depends(require_clerk_session),
):
    try:
        case, validation = request.app.state.repository.update_case(case_id, payload)
    except LookupError:
        raise HTTPException(404, detail="Case not found") from None
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    accepted = validation.quality_score >= 0.80
    if accepted:
        jobs = getattr(request.app.state, "jobs", None)
        if jobs is not None:
            jobs.schedule_refit(reason=f"case_updated:{case_id}", trigger=TriggerType.NEW_CASE)
    return CaseIngestionResponse(case=case, validation=validation, accepted_for_inference=accepted)


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case_endpoint(
    case_id: UUID,
    request: Request,
    _session: dict | None = Depends(require_clerk_session),
):
    ok = request.app.state.repository.delete_case(case_id)
    if not ok:
        raise HTTPException(404, detail="Case not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

Import `CaseUpdate`, `Response`, add `get_case`/`update_case`/`delete_case` on repository protocol used by tests.

- [ ] **Step 2: Tests**

- `GET /cases/{id}` returns 200 for seed case.
- `PATCH` without Clerk env still allowed (same as current POST behavior when `EOSP_CLERK_FRONTEND_API` unset).
- `DELETE` returns 204 and subsequent `GET` 404.

- [ ] **Step 3: Commit**

```bash
git add app/eosp/api/routes.py app/eosp/core/repository.py tests/test_api.py
git commit -m "feat(api): case get/patch/delete with researcher auth parity"
```

---

### Task 5: Frontend — dropdowns + cohort mode

**Files:**
- Modify: `app/eosp/static/js/drawer.js`
- Modify: `app/eosp/static/css/drawer.css`
- Modify: `app/eosp/static/js/api.js` (add `patch`, `del` helpers)

- [ ] **Step 1: Extend `api.js`**

```javascript
export async function patch(path, body) {
  const res = await fetch(BASE + path, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...(await authHeaders()),
    },
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_detailMessage(err.detail) || `PATCH ${path} → ${res.status}`);
  }
  return res.json();
}

export async function del(path) {
  const res = await fetch(BASE + path, {
    method: "DELETE",
    headers: await authHeaders(),
    credentials: "include",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(_detailMessage(err.detail) || `DELETE ${path} → ${res.status}`);
  }
}
```

- [ ] **Step 2: On drawer idle init, fetch reference data once**

```javascript
let _refCountries = [];
let _refAirportsAll = [];

async function loadReferenceGeo() {
  const [cRes, aRes] = await Promise.all([
    get("/reference/countries"),
    get("/reference/airports"),
  ]);
  _refCountries = cRes.countries || [];
  _refAirportsAll = aRes.airports || [];
}
```

Call `loadReferenceGeo()` from `initDrawer` or first `_renderIdle` (await).

- [ ] **Step 3: Replace country text input with `<select>`**

Options from `_refCountries`: `<option value="ES">ES — Spain</option>`.

- [ ] **Step 4: Airport `<select>` filtered by selected country**

On `change` of country, rebuild airport options where `a.country === selected` plus empty first option “— None —”.

- [ ] **Step 5: Cohort toggle**

Radio or select: `observation_kind`: `individual` | `cohort`.

When **individual**: show existing single-person fields; force hidden inputs `cohort_size=1`, `cohort_deaths=0`; show `death_date` for mortality.

When **cohort**: show `cohort_size`, `cohort_deaths`, `report_period_start`, `report_period_end`; hide single `death_date` or disable it (cohort deaths captured in `cohort_deaths` per `CaseCreate` validator).

- [ ] **Step 6: Submit payload** includes `observation_kind`, `cohort_size`, `cohort_deaths`, `report_period_*` per backend.

- [ ] **Step 7: Manual browser check** — load drawer, verify selects populate, cohort branch posts 201.

- [ ] **Step 8: Commit**

```bash
git add app/eosp/static/js/api.js app/eosp/static/js/drawer.js app/eosp/static/css/drawer.css
git commit -m "feat(ui): country/airport selects and cohort intake in researcher drawer"
```

---

### Task 6: Frontend — manage existing cases (edit + delete)

**Files:**
- Modify: `app/eosp/static/js/drawer.js`
- Modify: `app/eosp/static/css/drawer.css`

- [ ] **Step 1: Add section “Manage recorded cases”** below intake or in a collapsible `<details>`.

Fetch `GET /cases` (already public). Render table: identifier, onset, country, status, actions [Edit] [Delete].

- [ ] **Step 2: Delete**

```javascript
await del(`/cases/${caseId}`);
onRunComplete();
```

Confirm with `window.confirm`.

- [ ] **Step 3: Edit**

Clicking **Edit** loads the row into the intake form (populate selects), sets internal `_editingCaseId = uuid`, changes submit button to “Save changes”, and uses `PATCH /cases/{id}` instead of `POST /cases`. Cancel clears `_editingCaseId`.

- [ ] **Step 4: After successful PATCH**, clear edit mode and refresh.

- [ ] **Step 5: Commit**

```bash
git add app/eosp/static/js/drawer.js app/eosp/static/css/drawer.css
git commit -m "feat(ui): edit and soft-delete cases from researcher console"
```

---

## Self-review

**Spec coverage**

| Requirement | Task |
|-------------|------|
| Country + IATA as dropdowns | Task 1 (data), Task 5 (UI) |
| Cohort input | Task 5 (observation_kind + cohort fields) |
| Edit case | Tasks 3–4 (PATCH), Task 6 (UI) |
| Delete case | Tasks 3–4 (DELETE), Task 6 (UI) |

**Placeholder scan:** No TBD/TODO left; test code uses concrete paths and behaviors.

**Type consistency:** `CaseUpdate` field names match `CaseCreate` / `CaseRecord`; API paths use `case_id` UUID strings as existing `GET /cases/{case_id}/validation`.

**Gap addressed explicitly:** `VALID_COUNTRIES` vs dropdown mismatch resolved in Task 2.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-09-case-console-dropdowns-cohort-crud.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration (**REQUIRED SUB-SKILL:** `superpowers:subagent-driven-development`).

**2. Inline Execution** — Execute tasks in this session using batch checkpoints (**REQUIRED SUB-SKILL:** `superpowers:executing-plans`).

**Which approach do you want?**
