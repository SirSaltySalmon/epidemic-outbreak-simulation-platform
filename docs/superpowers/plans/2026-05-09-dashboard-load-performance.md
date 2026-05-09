# Dashboard load performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the EOSP dashboard initial reload faster for anonymous visitors and reduce origin load under many concurrent users by parallelizing boot, deferring console-only fetches, and enabling HTTP caching on safe GET routes.

**Architecture:** The static SPA currently awaits Clerk before any dashboard API calls, awaits a ~1 MB `/reference/airports` payload before `boot()` finishes, and forces `no-store` on both client and server. We parallelize CDN/script wait with dashboard GETs (Clerk still completes before `initDrawer` so console routes see tokens), lazy-load reference geo when the researcher drawer opens, and set `Cache-Control: public, max-age=…, stale-while-revalidate=…` on bundled reference data and short-TTL dashboard aggregates while leaving SSE and mutations uncached.

**Tech Stack:** FastAPI, vanilla ES modules (`main.js`, `drawer.js`, `api.js`), Pytest, Starlette `Response` headers.

---

## File structure

| File | Responsibility |
|------|----------------|
| `app/eosp/static/js/main.js` | Boot: run `initAuth()`, `_waitForLibs()`, and the six dashboard `get()` calls with maximum parallelism; await Clerk before `initDrawer`. |
| `app/eosp/static/js/drawer.js` | Remove eager `_loadReferenceGeo` from `initDrawer`; load on first `notifyDrawerOpened` via a shared promise; refresh case intake `<select>` options after load. |
| `app/eosp/static/js/api.js` | Stop passing `cache: "no-store"` on `get()` so browsers can honor server `Cache-Control`. |
| `app/eosp/api/routes.py` | Set cache headers on `public-config`, reference routes, `geo/outbreak`, `cases/summary`, `cases`, `forecasts/{scenario}`, `scenarios/compare`, `inference/versions`. |
| `tests/test_api.py` | Assert reference and summary responses expose `public` cache with `max-age`. |

---

### Task 1: API client — allow HTTP caching on GET

**Files:**
- Modify: `app/eosp/static/js/api.js`
- Test: manual / existing tests (no fetch cache assertions in CI)

- [ ] **Step 1: Remove forced `no-store` on `get()`**

```javascript
export async function get(path) {
  const res = await fetch(BASE + path, {
    headers: await authHeaders(),
    credentials: "include",
  });
```

- [ ] **Step 2: Commit**

```bash
git add app/eosp/static/js/api.js
git commit -m "fix(frontend): allow browser cache on GET per server headers"
```

---

### Task 2: Parallel dashboard boot (Clerk + libs + data)

**Files:**
- Modify: `app/eosp/static/js/main.js`

- [ ] **Step 1: Replace sequential `await initAuth()` + `await _waitForLibs()` + `Promise.allSettled` with parallel phase**

At the start of `boot()`:

```javascript
async function boot() {
  const authPromise = initAuth();
  const [, settled] = await Promise.all([
    _waitForLibs(),
    Promise.allSettled([
      get("/cases/summary"),
      get("/forecasts/baseline"),
      get("/inference/versions?limit=2"),
      get("/scenarios/compare?" + SCENARIOS_COMPARE_QUERY),
      get("/geo/outbreak"),
      get("/cases"),
    ]),
  ]);
  const [summary, forecast, versions, scenarios, geo, caseLines] = settled;
```

Keep the rest of `boot()` unchanged until the end: **after** map/`renderGeoData`, add `await authPromise` then `await initDrawer(_onRunComplete)`.

- [ ] **Step 2: Commit**

```bash
git add app/eosp/static/js/main.js
git commit -m "perf(frontend): parallelize Clerk, scripts, and dashboard API fetches"
```

---

### Task 3: Lazy-load reference countries/airports for the console

**Files:**
- Modify: `app/eosp/static/js/drawer.js`

- [ ] **Step 1: Add a shared loader promise and refresh helper**

After `_refAirportsAll` declarations:

```javascript
/** @type {Promise<void> | null} */
let _refGeoLoadPromise = null;

function _ensureReferenceGeoLoaded() {
  if (!_refGeoLoadPromise) {
    _refGeoLoadPromise = _loadReferenceGeo();
  }
  return _refGeoLoadPromise;
}

function _refreshCaseIntakeGeoSelects() {
  const form = document.querySelector("#drawer-body form.case-intake-form");
  if (!form) return;
  const cSel = form.querySelector('[name="location_country"]');
  if (!cSel || cSel.tagName !== "SELECT") return;
  const prev = cSel.value;
  cSel.innerHTML = _countrySelectOptions();
  if (prev && [...cSel.options].some((o) => o.value === prev)) {
    cSel.value = prev;
  }
  const apt = form.querySelector('[name="location_airport_code"]')?.value || "";
  _syncAirportSelect(form, apt);
}
```

- [ ] **Step 2: Change `initDrawer` — do not await `_loadReferenceGeo`**

```javascript
export async function initDrawer(onRunComplete) {
  _onRunCompleteCb = onRunComplete;
  await _renderIdle(onRunComplete);
}
```

- [ ] **Step 3: Load reference geo when the drawer opens**

```javascript
export async function notifyDrawerOpened() {
  await _ensureReferenceGeoLoaded();
  _refreshCaseIntakeGeoSelects();
  await _tryAttachActiveJob(_onRunCompleteCb);
}
```

- [ ] **Step 4: Commit**

```bash
git add app/eosp/static/js/drawer.js
git commit -m "perf(frontend): defer reference geo fetch until console opens"
```

---

### Task 4: Server — `Cache-Control` for safe GET routes

**Files:**
- Modify: `app/eosp/api/routes.py`

- [ ] **Step 1: Add module-level constants after `router = APIRouter()`**

```python
# Anonymous dashboard GETs: short TTL + SWR for traffic spikes (CDN/browser).
_CACHE_DASHBOARD_AGGREGATE = "public, max-age=30, stale-while-revalidate=120"
_CACHE_REFERENCE_BUNDLE = "public, max-age=3600, stale-while-revalidate=86400"
_CACHE_PUBLIC_CONFIG = "public, max-age=300"
```

- [ ] **Step 2: Inject `Response` and set headers**

Examples (apply to each handler):

- `public_config(response: Response)` → `_CACHE_PUBLIC_CONFIG`
- `reference_countries(http_response: Response)` → `_CACHE_REFERENCE_BUNDLE`
- `reference_airports(..., http_response: Response)` → `_CACHE_REFERENCE_BUNDLE`
- `geo_outbreak` → replace `no-store` with `_CACHE_DASHBOARD_AGGREGATE`
- `case_summary` → replace `no-store` with `_CACHE_DASHBOARD_AGGREGATE`
- `list_cases` → add `http_response: Response` and set `_CACHE_DASHBOARD_AGGREGATE`
- `forecast` → replace `no-store` with `_CACHE_DASHBOARD_AGGREGATE`
- `inference_versions` → replace `no-store` with `_CACHE_DASHBOARD_AGGREGATE`
- `scenario_comparison` → replace `no-store` with `_CACHE_DASHBOARD_AGGREGATE`

Leave `job_events_stream` and mutation routes unchanged.

- [ ] **Step 3: Commit**

```bash
git add app/eosp/api/routes.py
git commit -m "perf(api): short public cache for dashboard GETs and long cache for reference data"
```

---

### Task 5: Tests for cache headers

**Files:**
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

```python
def test_reference_countries_allows_public_cache():
    r = client.get("/api/v1/reference/countries")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "public" in cc
    assert "max-age=" in cc


def test_cases_summary_allows_public_cache():
    r = client.get("/api/v1/cases/summary")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "public" in cc
    assert "max-age=" in cc
```

- [ ] **Step 2: Run tests (expect FAIL until routes updated)**

Run: `pytest tests/test_api.py::test_reference_countries_allows_public_cache tests/test_api.py::test_cases_summary_allows_public_cache -v`

Expected: FAIL if headers missing.

- [ ] **Step 3: Implement route headers (Task 4) and re-run**

Run: `pytest tests/test_api.py -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_api.py
git commit -m "test(api): assert public Cache-Control on reference and summary routes"
```

---

## Self-review

1. **Spec coverage:** Parallel boot, lazy reference geo, client+server caching, tests — all covered.
2. **Placeholder scan:** None.
3. **Type consistency:** FastAPI `Response` parameter names match existing `http_response` pattern where already present; new handlers use `http_response: Response` for consistency with `geo_outbreak` / `case_summary`.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-09-dashboard-load-performance.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks.

**2. Inline Execution** — run tasks in this session with checkpoints.

**Which approach?**

*(This session implements Tasks 1–5 inline.)*
