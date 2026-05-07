# EOSP Frontend Redesign & Backend Extensions — Design Spec

**Date:** 2026-05-07  
**Status:** Approved for implementation  
**Scope:** Complete replacement of `app/eosp/static/` + additive backend routes and services

---

## Problem Statement

The current dashboard has fundamental usability failures:

1. **The "chart" is not a chart.** A `div` with `width: X%` styled as a horizontal bar conveys nothing about time, uncertainty, or trend. A visitor cannot understand outbreak trajectory from it.
2. **Numbers lack meaning.** `13 [7-22]` is displayed without explanation. A non-specialist cannot tell if this is good or bad, what the brackets mean, or over what timeframe.
3. **The forecast model is a black box.** When a researcher triggers a run, there is no feedback. No stages, no convergence diagnostics, no way to know if inference is working or hung.
4. **No world map.** The PRD specifies Leaflet + ship route + case heatmap + flight paths. None of this exists. The outbreak's global reach (through onward flight networks) is invisible.
5. **AI slop in the UI.** The "Run Forecasts" button sits exposed on the visitor view. Scenario names render as raw `evacuation_delay_7d`. The case intake form lives on the main dashboard. Empty-state text tells users what to click rather than showing data.
6. **Two user types conflated.** A visitor who should see a clean cached intelligence report and a researcher who needs to run live forecasts and watch the pipeline are given the same single-page jumble.

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Layout mode separation | Dashboard + Researcher Drawer (slide-in) | Visitor experience stays clean; researcher tools don't pollute it |
| Dashboard composition | KPIs → full-width world map → forecast chart + scenario sidebar | Map is the commanding visual; chart and scenarios share equal width below |
| Researcher pipeline UX | Collapsible stage cards | Each stage clearly visible; done stages collapse; active stage shows full diagnostics |
| Frontend stack | Vanilla JS + Plotly.js + Leaflet.js (CDN, no build step) | No toolchain overhead; FastAPI already serves static files |
| Geographic scope | Global risk propagation via OpenSky API (3-ring model) | Outbreak travels with passengers through the world flight network, not just 4 known destinations |
| Backend additions | All additive; no existing routes changed | Zero regression risk |

---

## Architecture

### Static file layout

```
app/eosp/static/
├── index.html              # Shell: topbar, map, chart, drawer mount
├── css/
│   ├── base.css            # Design tokens, reset, typography, layout grid
│   ├── dashboard.css       # KPI cards, map panel, chart panel, scenario sidebar
│   └── drawer.css          # Researcher drawer, stage cards, progress elements, mobile full-screen
└── js/
    ├── api.js              # fetch wrapper — GET/POST, error handling, abort controller
    ├── map.js              # Leaflet world map: ship marker, heatmap, flight arcs, popups
    ├── chart.js            # Plotly forecast timeseries: CI bands, observed points, scenario overlay
    ├── scenarios.js        # Scenario cards render + chart overlay on click
    ├── drawer.js           # Researcher drawer state machine (idle → running → done)
    ├── jobs.js             # SSE client for inference job progress events
    └── main.js             # Boot sequence, wires modules, handles resize/mobile
```

### Libraries (CDN, pinned versions)
- `plotly.js@2.35.2` — forecast chart, posterior histogram mini-chart in drawer
- `leaflet@1.9.4` — world map
- `leaflet-heat@0.2.0` — airport risk heatmap layer

### New Python files
```
app/eosp/services/
├── opensky.py          # OpenSky API client with Redis cache + static fallback
├── risk_propagation.py # Three-ring flight network risk scoring
└── geo.py              # Assembles geo/outbreak response; reads network_spec.json

app/eosp/api/
└── routes.py           # Extended: 4 new endpoints added
```

---

## Visitor Dashboard

### Topbar (fixed, always visible)
- **Left:** `EOSP` wordmark · `MV Hondius · Andes Hantavirus Outbreak` subtitle
- **Centre:** `Last updated: 8 min ago` — refreshes every 60s via polling `GET /api/v1/cases/summary`
- **Right:** Model version badge `v1.4.2` (green = converged, amber = warning, red = failed) · `⚗ Run Analysis →` button that opens the researcher drawer

### KPI strip (4 cards)

All values come from `GET /api/v1/cases/summary` and `GET /api/v1/forecasts/baseline`. Every number has a `?` tooltip explaining it in plain language.

| Card | Primary value | Sub-label | Tooltip text |
|---|---|---|---|
| Confirmed Cases | `3` | `+0 today · 4 countries` | *"Laboratory-confirmed via PCR or serology. These are certain cases."* |
| Suspected Cases | `5` | `PCR pending` | *"Clinically consistent with Andes hantavirus but not yet lab-confirmed."* |
| Deaths · CFR | `3 · 37%` | `Andes strain: 35–50% expected` | *"Case fatality rate. Andes hantavirus kills 35–50% of confirmed cases in published literature."* |
| 14-day Forecast | `13` | `95% CI: 7–22` | *"If we ran this simulation 100 times with slightly different parameters, 95 of those runs predicted a final count between 7 and 22."* |

### World map (full-width, 320px tall on desktop, 240px on mobile)

Leaflet with `CartoDB.DarkMatter` tile layer. All data comes from `GET /api/v1/geo/outbreak`. Zero compute on page load — the geo endpoint is cached.

**Layers (all toggleable via a layer control):**

1. **Ship marker** — animated pulsing circle at current known position. Tooltip: `MV Hondius · En route Tenerife · Day 32 of outbreak`. Position derived from known voyage timeline.

2. **Confirmed case markers** — `L.circleMarker` per country. Radius = `4 + (confirmed + suspected) * 3`. Fill: confirmed=`#b95b35`, suspected=`#d89c22`. Click opens popup: `South Africa (ZA) · 2 confirmed · 1 death · Cases arrived April 25`.

3. **Evacuation flight arcs** — Ring 1 only. Animated curved `L.Polyline` from ship position to each of the 3 evacuation destination airports. Dash-offset CSS animation gives a "flying" effect on load. Tooltip shows flight date and passenger count.

4. **Global risk heatmap** — `L.heatLayer` from `risk_heatmap` array in the geo response. Each airport's `[lat, lng, risk_score]` is a heat point. Gradient: transparent → amber → orange → red. Ring 2 and Ring 3 points are included at their computed risk score. On hover (using a transparent overlay `L.circleMarker` grid), shows popup: `London Heathrow (LHR) · Modelled exposure risk: medium · Based on onward flights from JNB within incubation window · Not a confirmed case`.

5. **Legend** (bottom-left): `● Confirmed case   ◌ Modelled exposure zone   — Evacuation flight arc`

### Forecast chart (left 65% of workspace below map)

Plotly.js `scatter` chart, dark background `#112820`, no gridlines except faint horizontal guides.

**Traces (bottom to top):**
1. `ci_95_lower` to `ci_95_upper` — fill `tonexty`, colour `rgba(11,124,131,0.12)`, no line
2. `ci_50_lower` to `ci_50_upper` — fill `tonexty`, colour `rgba(11,124,131,0.25)`, no line
3. Median line — solid `#0b7c83`, width 2
4. Observed cases — white `circle` markers, size 8, with error bars where `validation_score < 0.90`
5. Previous model version median — dashed grey line `#4a7a72`, width 1 (hindcast stability indicator). Fetched via `GET /api/v1/forecasts/baseline?model_version=<penultimate_version_id>` — the boot sequence loads both latest and the second entry from `GET /api/v1/inference/versions?limit=2`.

**Axes:** X = dates Apr 6 → May 21. Y = cumulative cases, starts at 0. No axis titles (the chart heading `Baseline Forecast · Cumulative cases · 14-day horizon` is sufficient). Hover tooltip: `May 14 · Median: 11 · 95% CI: 6–18 · 50% CI: 9–14 · Model v1.4.2`.

**Interactivity:** Clicking a scenario card in the sidebar overlays that scenario's median + 95% CI band in a second colour (green for improvement, red for worsening). A second click removes the overlay. Only one scenario overlay at a time.

**Plotly config:** `{ displayModeBar: false, responsive: true }` — no Plotly toolbar (it looks like AI slop). Zoom and pan are disabled for the visitor view; a researcher can toggle them in the drawer.

### Scenario sidebar (right 35% of workspace)

Four scenario cards stacked vertically. Each card:
- **Scenario name** (human-readable): `No Intervention · Baseline`, `Immediate Quarantine`, `Evacuation Delayed +7 Days`, `Enhanced Arrival Protocols`
- **Day-14 median** in large type
- **Delta badge** vs baseline: `↓ 62%` in green or `↑ 54%` in red
- **95% CI range** in small muted text: `95% CI: 2–11 cases`
- **One-line description:** *"All passengers confined to cabins from day 1 of outbreak"*
- Clicking the card triggers a scenario overlay on the forecast chart

A fifth card: `+ Custom Scenario` — greyed, tooltip: *"Define custom intervention parameters. Requires researcher access — click Run Analysis."*

---

## Researcher Drawer

### Trigger
`⚗ Run Analysis →` button in the topbar. Opens a right-side panel.

**Desktop:** 480px wide, full height, slides in from right with a 250ms ease-out transition. Main dashboard dims to 30% opacity. Dismissible with `✕` or `Escape`.

**Mobile (< 768px):** Full screen (100vw × 100vh). A back arrow `← Dashboard` at top-left replaces the `✕`. All stage cards have 48px minimum touch targets. Font sizes increase by 2px throughout.

### Idle state (no run in progress)

**Run configuration panel:**
- **Scenario selector:** Pill buttons for all 5 scenarios. Multi-select. Default: `Baseline` selected.
- **Simulation fidelity:** Three radio buttons — `100 (fast preview · ~2s)` / `1,000 (~10s)` / `10,000 (full fidelity · ~60s)`
- **Model version:** Dropdown showing last 5 versions. Default: `latest`.
- **`Run Forecast →` button:** Full width, teal, prominent.

**Last run summary:**
- Version ID, timestamp, trigger type (`manual` / `new_case` / `cron`)
- Parameter table: `p_transmit 0.089 ± 0.034` · `contacts/day 2.4` · `incubation 8.1d`
- Convergence badges: `R̂ 1.003 ✓` · `Divergences 23 ✓` · `ESS 412 ✓`

**Download panel:**
- `↓ Posterior samples (.nc)` — `posterior_download_url` from inference response
- `↓ Forecast JSON` — links to `GET /api/v1/forecasts/baseline`
- `↓ Hindcast validation report` — links to `GET /api/v1/validation/hindcast-accuracy`

### Running state — 4 collapsible stage cards

When `Run Forecast →` is clicked, `POST /api/v1/inference/run` is issued (with `reason: "manual"`). This returns `{ job_id, status: "scheduled" }`. That `job_id` is immediately used to open an SSE connection to `GET /api/v1/inference/jobs/{job_id}/events`. The full refresh pipeline (inference → simulation → forecast cache update) is what gets tracked through the stage cards.

**Card 1 — Cases & Network** (completes in ~1s, auto-collapses)
- Expanded: `8 cases loaded · mean quality 0.84` · contact network: `147 nodes · 892 edges`
- Horizontal breakdown bar: confirmed / suspected / deceased proportions
- Collapses to: `✓ Cases & Network · 8 cases · quality 0.84`

**Card 2 — Bayesian Inference (MCMC)**
- Three stat boxes: `DRAWS 1,240 / 2,000` · `R̂ MAX 1.003 ✓` · `DIVERGENCES 12 ✓`
- Thin progress bar with ETA: `~18 min remaining · 4 parallel chains`
- Live parameter chips (update on each SSE event): `p_transmit 0.089` · `contacts/day 2.4` · `incubation 8.1d`
- Mini Plotly histogram: posterior distribution of `p_transmit` forming in real time. Rendered as a 200×80px chart, updated every 10 SSE events to avoid render thrashing.
- Chain status row: `Chain 1 ●` `Chain 2 ●` `Chain 3 ●` `Chain 4 ●` — green if R̂ < 1.01, amber if < 1.05, red if ≥ 1.05
- Collapses to: `✓ Inference complete · R̂ 1.003 · 23 divergences · p_transmit 0.089`

**Card 3 — Monte Carlo Simulation**
- Large counter: `4,312 / 10,000 trajectories` animated tick
- Per-scenario pill progress bars (one per selected scenario)
- Mini fan-chart: 20 sampled trajectories as faint lines converging into the ensemble. Updates every 500 trajectories. Rendered in a 200×80px Plotly chart.
- ETA: `~45 seconds remaining`
- Collapses to: `✓ Simulation complete · 10,000 trajectories · 5 scenarios`

**Card 4 — Results Ready**
- Green banner: `Forecast complete · v1.4.3 · generated 22:47 UTC`
- Change summary vs previous version: `p_transmit ↑14% · Median Day-14: 13→15 cases`
- `← View updated dashboard` button — closes drawer, main chart animates to new forecast data via `Plotly.react()`

### SSE event schema

Each event from `GET /api/v1/inference/jobs/{job_id}/events`:

```json
{ "stage": "cases",       "status": "complete", "n_cases": 8, "quality_mean": 0.84 }
{ "stage": "inference",   "status": "running",  "draw": 1240, "total": 2000,
  "rhat_max": 1.003, "divergences": 12,
  "params": { "p_transmit": 0.089, "contacts_daily": 2.4, "incubation": 8.1 },
  "chain_rhat": [1.002, 1.003, 1.001, 1.004] }
{ "stage": "simulation",  "status": "running",  "trajectories": 4312, "total": 10000,
  "scenario": "baseline",
  "fan_sample": [[8,8,9,9,10,11,12,13,13,14], [7,8,8,9,11,12,13,14,15,16], ...]
  // fan_sample: list of 10 arrays, each array is cumulative case counts for days 1–n_days
  // sampled randomly from completed trajectories so far
}
{ "stage": "complete",    "status": "done",
  "model_version": "v1.4.3", "forecast_ready": true }
```

The drawer JS handles each `stage` field to update the appropriate card. Unknown stages are silently ignored for forward compatibility.

---

## Backend Additions (all additive)

### 1. `GET /api/v1/geo/outbreak`

**File:** `app/eosp/api/routes.py` (new route) + `app/eosp/services/geo.py` (new service)

**Logic:** Reads `network_spec.json` for ship/flight data. Calls `risk_propagation.compute_risk_zones()` which internally calls `opensky.get_onward_flights()`. Returns the assembled response. Cached with a 12-hour TTL in the repository's in-memory cache (same pattern as forecast cache).

**Response:** As specified in the revised Section 4 above. `risk_heatmap` is the full list of `[airport_iata, lat, lng, risk_score, ring]` entries.

### 2. `app/eosp/services/opensky.py`

Uses `opensky-api` Python package. Key method: `get_onward_flights(airport_icao, after_unix, window_days=7)` → list of `{callsign, dest_airport_icao, est_arr_time}`. A hardcoded coordinate table maps ICAO/IATA codes to `{lat, lng, city, country}` for the 200 most common international airports (sufficient for coverage without any external lookup).

Falls back to returning only the 3 known evacuation destinations if the API is unavailable or rate-limited.

### 3. `app/eosp/services/risk_propagation.py`

`compute_risk_zones(p_transmit, evacuation_flights, onward_flights)` → `list[RiskZone]`

Risk score formula per airport:
- Ring 1: `n_evacuees_on_flight / total_passengers × p_transmit × 0.89` (direct exposure)
- Ring 2: `ring1_score[origin] × flight_occupancy × p_transmit × 0.30` (one hop)
- Ring 3: `ring2_score[origin] × flight_occupancy × p_transmit × 0.10` (two hops, low confidence)

Scores normalised to 0–1 within each ring. Ring 3 is only included if `ring3_score > 0.05`.

### 4. `GET /api/v1/inference/jobs/{job_id}/events` (SSE)

**File:** `app/eosp/api/routes.py` (new route)

```python
@router.get("/inference/jobs/{job_id}/events")
async def job_events_stream(job_id: str, request: Request):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Job manager unavailable")
    async def generate():
        while True:
            if await request.is_disconnected():
                break
            events = jobs.drain_events(job_id)  # pops all pending events, returns list[dict]
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"
            record = jobs.get_status(job_id)
            if record and record.status in ("completed", "failed"):
                yield f"data: {json.dumps({'stage': 'complete', 'status': record.status})}\n\n"
                break
            await asyncio.sleep(0.5)
        yield "data: {\"type\": \"close\"}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

**`JobRecord` extension** (`services/jobs.py`):
- Add field `progress_events: collections.deque = field(default_factory=lambda: collections.deque(maxlen=200))`
- Add method `push_event(self, event: dict) -> None` — appends to the deque
- Add method `drain_events(self) -> list[dict]` — pops and returns all current items (empties the deque)

**`JobManager` extension** (`services/jobs.py`):
- Add method `push_event(self, job_id: str, event: dict) -> None` — looks up the record and calls `record.push_event(event)`. No-op if job_id not found.
- Add method `drain_events(self, job_id: str) -> list[dict]` — looks up the record and calls `record.drain_events()`. Returns `[]` if not found.

### 5. `ForecastPoint` model extension (`core/models.py`)

Two new optional fields:

```python
class ForecastPoint(BaseModel):
    day: int
    date: date
    cases_cumulative: dict[str, float]   # gains ci_50_lower, ci_50_upper alongside existing ci_95_*
    cases_new: dict[str, float]
    deaths_cumulative: dict[str, float]
    location_breakdown: dict[str, dict[str, float]] | None = None
```

**`ci_50_lower` / `ci_50_upper`:** The ensemble runner (`services/ensemble.py`) currently computes `np.percentile` at [2.5, 25, 50, 75, 97.5]. The 25th and 75th percentile values are already computed but not surfaced. Add `"ci_50_lower": float(np.percentile(counts, 25))` and `"ci_50_upper": float(np.percentile(counts, 75))` to the metrics dict in `_aggregate_trajectories()`.

**`location_breakdown`:** The ensemble runner tracks per-cluster agent counts each day in `_run_single()`. Aggregate these the same way as global counts and surface them. Keys are the cluster names from `network_spec.json`: `ship`, `ZA_JNB`, `NL_AMS`, `CH_GVA`, `ES_TFN`.

### 6. Inference/simulation progress hooks

**`services/inference.py`:** After every 100 simulated draws, call:
```python
jobs.push_event(job_id, {
    "stage": "inference", "status": "running",
    "draw": current_draw, "total": total_draws,
    "rhat_max": current_rhat, "divergences": div_count,
    "params": {"p_transmit": ..., "contacts_daily": ..., "incubation": ...},
    "chain_rhat": [...]
})
```

**`services/ensemble.py`:** After every 500 trajectories, call:
```python
jobs.push_event(job_id, {
    "stage": "simulation", "status": "running",
    "trajectories": completed, "total": n_simulations,
    "scenario": current_scenario,
    "fan_sample": sample_of_10_trajectories_as_lists
})
```

In the current dev implementation (seeded deterministic runs), these events are emitted via `asyncio.sleep(0)` interleaving to simulate realistic progress. The hook signature is the same so production Bayesian inference slots in without changing the routes layer.

---

## What Gets Removed

| Current element | Reason for removal | Replacement |
|---|---|---|
| Horizontal CSS bar chart | Not a chart; conveys no time/uncertainty information | Plotly timeseries with CI bands |
| `renderForecastEmptyState()` instructional text | Visitors should never see an empty state | Forecast cache is always prewarmed on startup |
| `Run Forecasts` button on main page | Visitors must not trigger compute | Researcher drawer only |
| Raw scenario name strings (`evacuation_delay_7d`) | Unreadable to non-technical users | Human-readable labels |
| Case intake form on dashboard | Admin function, not a visitor view | Researcher drawer → Downloads section |
| `13 [7-22]` unlabelled number string | Meaningless without context | KPI card with tooltip + labelled CI |
| `renderForecastRuns()` run list | Low-signal noise output | Results appear in drawer's Stage 4 card |

---

## Responsive behaviour

| Breakpoint | Behaviour |
|---|---|
| ≥ 1200px | Full layout: map full-width, chart 65% + scenario sidebar 35% |
| 768–1199px | Map full-width, chart full-width below map, scenario cards horizontal scroll row |
| < 768px | Map 200px tall, chart full-width (touch zoom enabled), scenario cards stack vertically, drawer is full-screen |

---

## Open questions (resolved)

- **OpenSky free tier (400 calls/day):** Handled by aggressive Redis/memory caching (12h TTL). A single geo endpoint call consumes ≤ 6 OpenSky calls (one per Ring 1 airport × 2 for arrivals/departures). Cache means most page loads hit zero OpenSky calls.
- **Plotly bundle size:** `plotly.js` is ~3.5MB. Acceptable for an epidemiologist tool on desktop. A `plotly-basic` CDN build (~1MB) covers scatter + histogram and will be used instead.
- **No auth wall on researcher drawer:** The drawer is accessible to anyone who has the URL. Role-based access is a PRD Phase 2 item; for now, the `⚗ Run Analysis` button is available to all but compute is finite.
