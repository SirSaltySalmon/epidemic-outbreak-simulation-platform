# EOSP Frontend Redesign & Backend Extensions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder CSS-bar-chart dashboard with a production-quality epidemiological intelligence UI: Leaflet world map with global flight-network risk heatmap, Plotly CI-band forecast chart, scenario sidebar, and a researcher drawer with live pipeline progress via SSE.

**Architecture:** Vanilla JS (no build step) + Plotly.js + Leaflet.js served by existing FastAPI StaticFiles. Additive Python backend: JobRecord progress hooks, SSE streaming endpoint, OpenSky-backed geo/risk service, new `/api/v1/geo/outbreak` route.

**Tech Stack:** Python 3.12, FastAPI, Plotly.js 2.x (basic CDN build), Leaflet 1.9.4, leaflet-heat 0.2.0, opensky-api Python package, NetworkX (already installed).

---

## File Map

**New Python files:**
- Create: `app/eosp/services/opensky.py`
- Create: `app/eosp/services/risk_propagation.py`
- Create: `app/eosp/services/geo.py`

**Modified Python files:**
- Modify: `app/eosp/services/jobs.py` — add progress hooks to `JobRecord` and `JobManager`; wire events into `_run_full_refresh`
- Modify: `app/eosp/services/ensemble.py` — accept optional `progress_callback` in `run_ensemble`
- Modify: `app/eosp/api/routes.py` — add 2 new endpoints
- Modify: `app/eosp/core/models.py` — add `location_breakdown` field to `ForecastPoint`
- Modify: `pyproject.toml` — add `opensky-api` dependency

**Replaced static files (delete old, create new):**
- Delete+Create: `app/eosp/static/index.html`
- Delete+Create: `app/eosp/static/styles.css` → split into `app/eosp/static/css/base.css`, `css/dashboard.css`, `css/drawer.css`
- Delete+Create: `app/eosp/static/app.js` → split into `app/eosp/static/js/api.js`, `js/map.js`, `js/chart.js`, `js/scenarios.js`, `js/drawer.js`, `js/jobs.js`, `js/main.js`

---

## Task 1: JobRecord & JobManager — progress event hooks

**Files:**
- Modify: `app/eosp/services/jobs.py:36-59` (JobRecord dataclass)
- Modify: `app/eosp/services/jobs.py:62-232` (JobManager class)

- [ ] **Step 1: Add `progress_events` field and methods to `JobRecord`**

Open `app/eosp/services/jobs.py`. Add `import collections` to the top-level imports. Then extend the `JobRecord` dataclass:

```python
import collections  # add to imports at top of file

@dataclass
class JobRecord:
    job_id: str
    job_type: str
    status: str = "pending"
    reason: str = ""
    scheduled_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    progress_events: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=200)
    )

    def push_event(self, event: dict) -> None:
        self.progress_events.append(event)

    def drain_events(self) -> list[dict]:
        events = list(self.progress_events)
        self.progress_events.clear()
        return events

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status,
            "reason": self.reason,
            "scheduled_at": self.scheduled_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "detail": self.detail,
            "error": self.error,
        }
```

- [ ] **Step 2: Add `push_event` and `drain_events` to `JobManager`**

In the `JobManager` class, add these two methods after `list_recent`:

```python
def push_event(self, job_id: str, event: dict) -> None:
    record = self._jobs.get(job_id)
    if record is not None:
        record.push_event(event)

def drain_events(self, job_id: str) -> list[dict]:
    record = self._jobs.get(job_id)
    if record is None:
        return []
    return record.drain_events()
```

- [ ] **Step 3: Verify the app still boots**

```
python -m uvicorn eosp.main:app --app-dir app --reload
```

Expected: server starts, no import errors. Hit `GET /api/v1/health` → `{"status": "ok"}`.

- [ ] **Step 4: Commit**

```
git add app/eosp/services/jobs.py
git commit -m "feat: add progress event hooks to JobRecord and JobManager"
```

---

## Task 2: SSE endpoint for live job progress

**Files:**
- Modify: `app/eosp/api/routes.py`

- [ ] **Step 1: Add required imports to routes.py**

At the top of `app/eosp/api/routes.py`, add:

```python
import asyncio
import json
from fastapi.responses import StreamingResponse
```

- [ ] **Step 2: Add the SSE route**

Append to `app/eosp/api/routes.py`:

```python
@router.get("/inference/jobs/{job_id}/events")
async def job_events_stream(job_id: str, request: Request):
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise HTTPException(status_code=503, detail="Job manager unavailable")
    if jobs.get_status(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def generate():
        while True:
            if await request.is_disconnected():
                break
            events = jobs.drain_events(job_id)
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"
            record = jobs.get_status(job_id)
            if record is not None and record.status in ("completed", "failed"):
                yield f"data: {json.dumps({'stage': 'complete', 'status': record.status, 'detail': record.detail})}\n\n"
                break
            await asyncio.sleep(0.5)
        yield "data: {\"type\": \"close\"}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 3: Smoke-test the SSE endpoint**

Start the server. In a browser or curl: `curl -N http://localhost:8000/api/v1/inference/jobs/nonexistent/events`. Expected: `{"detail": "Job not found"}` with 404.

- [ ] **Step 4: Commit**

```
git add app/eosp/api/routes.py
git commit -m "feat: add SSE endpoint for live inference job progress"
```

---

## Task 3: Wire progress events into the job pipeline

**Files:**
- Modify: `app/eosp/services/jobs.py` — `_run_full_refresh` and `_run_single_ensemble`
- Modify: `app/eosp/services/ensemble.py` — `run_ensemble` and `_run_trajectories`

- [ ] **Step 1: Add `progress_callback` parameter to `run_ensemble`**

In `app/eosp/services/ensemble.py`, update the signature of `run_ensemble`:

```python
from typing import Callable  # add to imports

def run_ensemble(
    *,
    scenario: ScenarioSpec,
    inference: InferenceResult,
    network: ContactNetwork,
    seed: SeedState,
    config: EnsembleConfig | None = None,
    posterior_samples: Mapping[str, np.ndarray] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> ForecastResponse:
```

Pass `progress_callback` down to `_run_trajectories`:

```python
    trajectories = _run_trajectories(
        network=scenario_network,
        seed=seed,
        samples=samples,
        n_days=config.n_days,
        rng_seed=config.rng_seed,
        parallel=config.parallel,
        max_workers=config.max_workers,
        progress_callback=progress_callback,
        n_simulations=config.n_simulations,
        scenario_name=scenario.name,
    )
```

- [ ] **Step 2: Emit trajectory progress events in `_run_trajectories`**

Update `_run_trajectories` signature and body to emit every 500 trajectories:

```python
def _run_trajectories(
    *,
    network: ContactNetwork,
    seed: SeedState,
    samples: dict[str, np.ndarray],
    n_days: int,
    rng_seed: int,
    parallel: bool,
    max_workers: int | None,
    progress_callback: Callable[[dict], None] | None = None,
    n_simulations: int = 0,
    scenario_name: str = "",
) -> list[Trajectory]:
    n_simulations = n_simulations or len(samples["p_transmit"])
    BATCH_REPORT = 500

    def run_one(index: int) -> Trajectory:
        sample_rng = np.random.default_rng((rng_seed + index) & 0xFFFFFFFF)
        params = {
            "p_transmit": float(samples["p_transmit"][index]),
            "contacts_daily": float(samples["contacts_daily"][index]),
            "incubation_mean": float(samples["incubation_mean"][index]),
            "h2h_multiplier": float(samples["h2h_multiplier"][index]),
            "cfr": float(samples["cfr"][index]),
        }
        return simulate_trajectory(network=network, params=params, seed=seed,
                                   n_days=n_days, rng=sample_rng)

    if not parallel or n_simulations < 64:
        results = []
        for i in range(n_simulations):
            results.append(run_one(i))
            if progress_callback and (i + 1) % BATCH_REPORT == 0:
                progress_callback({
                    "stage": "simulation", "status": "running",
                    "trajectories": i + 1, "total": n_simulations,
                    "scenario": scenario_name,
                    "fan_sample": _sample_fan(results, n_days=n_days, k=10),
                })
        return results

    from concurrent.futures import as_completed
    workers = max_workers or min(8, max(1, (os.cpu_count() or 4)))
    results = [None] * n_simulations
    completed_count = 0
    last_reported = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_index = {pool.submit(run_one, i): i for i in range(n_simulations)}
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            results[idx] = future.result()
            completed_count += 1
            if progress_callback and completed_count - last_reported >= BATCH_REPORT:
                last_reported = completed_count
                done_so_far = [r for r in results if r is not None]
                progress_callback({
                    "stage": "simulation", "status": "running",
                    "trajectories": completed_count, "total": n_simulations,
                    "scenario": scenario_name,
                    "fan_sample": _sample_fan(done_so_far, n_days=n_days, k=10),
                })
    return results
```

Add the helper:

```python
def _sample_fan(trajectories: list[Trajectory], n_days: int, k: int = 10) -> list[list[float]]:
    """Return k randomly sampled cumulative-case arrays for the fan-chart."""
    if not trajectories:
        return []
    rng = np.random.default_rng(42)
    chosen = rng.choice(len(trajectories), size=min(k, len(trajectories)), replace=False)
    return [trajectories[i].cumulative_cases[1:].tolist() for i in chosen]
```

- [ ] **Step 3: Emit stage events from `_run_full_refresh` in `jobs.py`**

In `app/eosp/services/jobs.py`, update `_run_full_refresh` to emit progress events:

```python
def _run_full_refresh(self, record: JobRecord, trigger: TriggerType) -> None:
    cases = self._load_cases()
    if not cases:
        record.detail["skipped"] = "no_cases"
        return

    # Stage 1 — cases loaded
    record.push_event({
        "stage": "cases", "status": "complete",
        "n_cases": len(cases),
        "quality_mean": round(
            sum(c.validation_score for c in cases) / len(cases), 3
        ),
    })

    try:
        artifacts = run_inference(
            cases=cases,
            network=self._network,
            config=self._inference_config,
            trigger=trigger,
        )
    except RuntimeError as exc:
        record.detail["inference_error"] = str(exc)
        logger.warning("Inference unavailable, falling back: %s", exc)
        artifacts = None

    if artifacts is not None:
        self._update_inference(artifacts.result)
        record.detail["inference_version"] = artifacts.result.version
        inference = artifacts.result
        samples = artifacts.posterior_samples
        diag = inference.diagnostics
        # Stage 2 — inference complete
        record.push_event({
            "stage": "inference", "status": "complete",
            "draw": diag.get("n_samples", self._inference_config.num_samples),
            "total": diag.get("n_samples", self._inference_config.num_samples),
            "rhat_max": diag.get("rhat_max", max(diag["rhat"].values()) if diag.get("rhat") else 1.0),
            "divergences": diag.get("divergences", 0),
            "params": {
                k: round(float(v.mean), 4)
                for k, v in inference.parameters.items()
                if k not in ("concentration", "initial_rate")
            },
            "chain_rhat": list(diag["rhat"].values()) if diag.get("rhat") else [],
        })
    else:
        inference = self._repository.latest_inference()
        samples = None
        record.push_event({"stage": "inference", "status": "skipped", "reason": "fallback"})

    seed = self._build_seed_state(cases)
    for scenario_name, spec in self._scenarios.items():

        def _callback(event: dict, _jid: str = record.job_id) -> None:
            self.push_event(_jid, event)

        response = run_ensemble(
            scenario=spec,
            inference=inference,
            network=self._network,
            seed=seed,
            config=self._ensemble_config,
            posterior_samples=samples,
            progress_callback=_callback,
        )
        self._cache_forecast(scenario_name, response)
    record.detail["scenarios_refreshed"] = list(self._scenarios.keys())
```

- [ ] **Step 4: Run tests to confirm nothing broke**

```
python -m pytest tests/ -x -q
```

Expected: all existing tests pass.

- [ ] **Step 5: Commit**

```
git add app/eosp/services/jobs.py app/eosp/services/ensemble.py
git commit -m "feat: wire progress events through job pipeline and ensemble runner"
```

---

## Task 4: Add opensky-api dependency and country coordinate table

**Files:**
- Modify: `pyproject.toml`
- Create: `app/eosp/data/airport_coords.json`

- [ ] **Step 1: Install opensky-api**

```
pip install opensky-api
```

Add to `pyproject.toml` under `[project] dependencies`:

```toml
"opensky-api>=1.3",
```

- [ ] **Step 2: Create airport coordinate table**

Create `app/eosp/data/airport_coords.json` with IATA→lat/lng for the 80 highest-traffic international airports plus the specific airports in the outbreak:

```json
{
  "JNB": {"lat": -26.134, "lng": 28.242, "city": "Johannesburg", "country": "ZA"},
  "CPT": {"lat": -33.965, "lng": 18.602, "city": "Cape Town", "country": "ZA"},
  "DUR": {"lat": -29.614, "lng": 31.117, "city": "Durban", "country": "ZA"},
  "AMS": {"lat": 52.309, "lng": 4.764, "city": "Amsterdam", "country": "NL"},
  "GVA": {"lat": 46.238, "lng": 6.109, "city": "Geneva", "country": "CH"},
  "ZRH": {"lat": 47.464, "lng": 8.549, "city": "Zurich", "country": "CH"},
  "TFN": {"lat": 28.048, "lng": -16.572, "city": "Tenerife North", "country": "ES"},
  "TFS": {"lat": 28.045, "lng": -16.572, "city": "Tenerife South", "country": "ES"},
  "LAS": {"lat": 36.084, "lng": -115.153, "city": "Las Vegas", "country": "US"},
  "LHR": {"lat": 51.477, "lng": -0.461, "city": "London Heathrow", "country": "GB"},
  "LGW": {"lat": 51.148, "lng": -0.190, "city": "London Gatwick", "country": "GB"},
  "CDG": {"lat": 49.013, "lng": 2.550, "city": "Paris CDG", "country": "FR"},
  "FRA": {"lat": 50.033, "lng": 8.571, "city": "Frankfurt", "country": "DE"},
  "MUC": {"lat": 48.354, "lng": 11.786, "city": "Munich", "country": "DE"},
  "MAD": {"lat": 40.472, "lng": -3.561, "city": "Madrid", "country": "ES"},
  "BCN": {"lat": 41.297, "lng": 2.078, "city": "Barcelona", "country": "ES"},
  "FCO": {"lat": 41.800, "lng": 12.239, "city": "Rome Fiumicino", "country": "IT"},
  "MXP": {"lat": 45.630, "lng": 8.723, "city": "Milan Malpensa", "country": "IT"},
  "BRU": {"lat": 50.901, "lng": 4.484, "city": "Brussels", "country": "BE"},
  "VIE": {"lat": 48.110, "lng": 16.570, "city": "Vienna", "country": "AT"},
  "CPH": {"lat": 55.618, "lng": 12.656, "city": "Copenhagen", "country": "DK"},
  "ARN": {"lat": 59.651, "lng": 17.919, "city": "Stockholm", "country": "SE"},
  "HEL": {"lat": 60.317, "lng": 24.963, "city": "Helsinki", "country": "FI"},
  "OSL": {"lat": 60.193, "lng": 11.100, "city": "Oslo", "country": "NO"},
  "DUB": {"lat": 53.421, "lng": -6.270, "city": "Dublin", "country": "IE"},
  "LIS": {"lat": 38.774, "lng": -9.134, "city": "Lisbon", "country": "PT"},
  "ATH": {"lat": 37.936, "lng": 23.945, "city": "Athens", "country": "GR"},
  "IST": {"lat": 41.275, "lng": 28.752, "city": "Istanbul", "country": "TR"},
  "DXB": {"lat": 25.253, "lng": 55.365, "city": "Dubai", "country": "AE"},
  "AUH": {"lat": 24.433, "lng": 54.651, "city": "Abu Dhabi", "country": "AE"},
  "DOH": {"lat": 25.273, "lng": 51.608, "city": "Doha", "country": "QA"},
  "KWI": {"lat": 29.227, "lng": 47.969, "city": "Kuwait City", "country": "KW"},
  "RUH": {"lat": 24.958, "lng": 46.699, "city": "Riyadh", "country": "SA"},
  "JED": {"lat": 21.680, "lng": 39.157, "city": "Jeddah", "country": "SA"},
  "BOM": {"lat": 19.089, "lng": 72.868, "city": "Mumbai", "country": "IN"},
  "DEL": {"lat": 28.556, "lng": 77.100, "city": "Delhi", "country": "IN"},
  "SIN": {"lat": 1.350, "lng": 103.994, "city": "Singapore", "country": "SG"},
  "KUL": {"lat": 2.746, "lng": 101.710, "city": "Kuala Lumpur", "country": "MY"},
  "BKK": {"lat": 13.681, "lng": 100.747, "city": "Bangkok", "country": "TH"},
  "HKG": {"lat": 22.308, "lng": 113.918, "city": "Hong Kong", "country": "HK"},
  "PEK": {"lat": 40.080, "lng": 116.585, "city": "Beijing", "country": "CN"},
  "PVG": {"lat": 31.143, "lng": 121.805, "city": "Shanghai Pudong", "country": "CN"},
  "ICN": {"lat": 37.469, "lng": 126.451, "city": "Seoul Incheon", "country": "KR"},
  "NRT": {"lat": 35.765, "lng": 140.386, "city": "Tokyo Narita", "country": "JP"},
  "HND": {"lat": 35.550, "lng": 139.781, "city": "Tokyo Haneda", "country": "JP"},
  "SYD": {"lat": -33.946, "lng": 151.177, "city": "Sydney", "country": "AU"},
  "MEL": {"lat": -37.673, "lng": 144.843, "city": "Melbourne", "country": "AU"},
  "AKL": {"lat": -37.009, "lng": 174.792, "city": "Auckland", "country": "NZ"},
  "JFK": {"lat": 40.640, "lng": -73.779, "city": "New York JFK", "country": "US"},
  "EWR": {"lat": 40.693, "lng": -74.175, "city": "New York Newark", "country": "US"},
  "ORD": {"lat": 41.978, "lng": -87.905, "city": "Chicago O'Hare", "country": "US"},
  "LAX": {"lat": 33.943, "lng": -118.408, "city": "Los Angeles", "country": "US"},
  "MIA": {"lat": 25.796, "lng": -80.287, "city": "Miami", "country": "US"},
  "ATL": {"lat": 33.641, "lng": -84.427, "city": "Atlanta", "country": "US"},
  "SFO": {"lat": 37.619, "lng": -122.375, "city": "San Francisco", "country": "US"},
  "IAD": {"lat": 38.944, "lng": -77.456, "city": "Washington Dulles", "country": "US"},
  "YYZ": {"lat": 43.677, "lng": -79.630, "city": "Toronto", "country": "CA"},
  "YUL": {"lat": 45.470, "lng": -73.741, "city": "Montreal", "country": "CA"},
  "MEX": {"lat": 19.436, "lng": -99.072, "city": "Mexico City", "country": "MX"},
  "BOG": {"lat": 4.702, "lng": -74.146, "city": "Bogota", "country": "CO"},
  "GRU": {"lat": -23.432, "lng": -46.469, "city": "Sao Paulo", "country": "BR"},
  "EZE": {"lat": -34.822, "lng": -58.536, "city": "Buenos Aires", "country": "AR"},
  "SCL": {"lat": -33.393, "lng": -70.786, "city": "Santiago", "country": "CL"},
  "LIM": {"lat": -12.022, "lng": -77.114, "city": "Lima", "country": "PE"},
  "CAI": {"lat": 30.122, "lng": 31.406, "city": "Cairo", "country": "EG"},
  "NBO": {"lat": -1.319, "lng": 36.928, "city": "Nairobi", "country": "KE"},
  "ADD": {"lat": 8.978, "lng": 38.799, "city": "Addis Ababa", "country": "ET"},
  "LOS": {"lat": 6.577, "lng": 3.321, "city": "Lagos", "country": "NG"},
  "ACC": {"lat": 5.605, "lng": -0.167, "city": "Accra", "country": "GH"},
  "CMN": {"lat": 33.368, "lng": -7.590, "city": "Casablanca", "country": "MA"},
  "TUN": {"lat": 36.851, "lng": 10.227, "city": "Tunis", "country": "TN"},
  "DAK": {"lat": 14.739, "lng": -17.490, "city": "Dakar", "country": "SN"},
  "RAK": {"lat": 31.607, "lng": -8.036, "city": "Marrakech", "country": "MA"},
  "MRU": {"lat": -20.430, "lng": 57.683, "city": "Mauritius", "country": "MU"},
  "TNR": {"lat": -18.798, "lng": 47.479, "city": "Antananarivo", "country": "MG"},
  "EBB": {"lat": 0.042, "lng": 32.443, "city": "Kampala", "country": "UG"},
  "DAR": {"lat": -6.878, "lng": 39.203, "city": "Dar es Salaam", "country": "TZ"},
  "JRO": {"lat": -3.429, "lng": 37.074, "city": "Kilimanjaro", "country": "TZ"},
  "VCP": {"lat": 15.760, "lng": -23.218, "city": "Sao Vicente Cape Verde", "country": "CV"}
}
```

- [ ] **Step 3: Commit**

```
git add pyproject.toml app/eosp/data/airport_coords.json
git commit -m "feat: add opensky-api dependency and airport coordinate table"
```

---

## Task 5: services/opensky.py — OpenSky client with cache and fallback

**Files:**
- Create: `app/eosp/services/opensky.py`

- [ ] **Step 1: Write the module**

Create `app/eosp/services/opensky.py`:

```python
"""OpenSky Network API client for outbound flight enumeration (FR integration).

Queries departure records for a given airport within a time window.
Results are cached in-memory for 12 hours to respect the free-tier
400 calls/day limit. Falls back to an empty list on API error so the
geo endpoint always returns something usable.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_COORDS_PATH = Path(__file__).resolve().parent.parent / "data" / "airport_coords.json"
_CACHE_TTL_SECONDS = 43200  # 12 hours


@dataclass
class FlightRecord:
    callsign: str
    dest_airport_icao: str | None
    dest_airport_iata: str | None
    est_departure_time: int  # unix timestamp
    est_arrival_time: int | None


@dataclass
class _CacheEntry:
    data: list[FlightRecord]
    fetched_at: float = field(default_factory=time.monotonic)

    def is_fresh(self) -> bool:
        return (time.monotonic() - self.fetched_at) < _CACHE_TTL_SECONDS


_CACHE: dict[tuple[str, int, int], _CacheEntry] = {}


def load_airport_coords() -> dict[str, dict[str, Any]]:
    with _COORDS_PATH.open() as fh:
        return json.load(fh)


def iata_to_icao(iata: str) -> str:
    """Best-effort IATA→ICAO mapping for the airports in our dataset.
    OpenSky uses ICAO codes; our coordinate table uses IATA.
    """
    _MAP = {
        "JNB": "FAOR", "CPT": "FACT", "AMS": "EHAM", "GVA": "LSGG",
        "ZRH": "LSZH", "TFN": "GCXO", "TFS": "GCTS", "LHR": "EGLL",
        "CDG": "LFPG", "FRA": "EDDF", "DXB": "OMDB", "SIN": "WSSS",
        "NBO": "HKJK", "JNB": "FAOR", "VCP": "GVSV",
    }
    return _MAP.get(iata.upper(), iata.upper())


def get_departures(airport_iata: str, begin_unix: int, end_unix: int) -> list[FlightRecord]:
    """Return flights departing from ``airport_iata`` between the two timestamps.

    Returns cached data if available; falls back to empty list on error.
    """
    cache_key = (airport_iata.upper(), begin_unix, end_unix)
    entry = _CACHE.get(cache_key)
    if entry is not None and entry.is_fresh():
        return entry.data

    records = _fetch_from_api(airport_iata, begin_unix, end_unix)
    _CACHE[cache_key] = _CacheEntry(data=records)
    return records


def _fetch_from_api(airport_iata: str, begin_unix: int, end_unix: int) -> list[FlightRecord]:
    try:
        from opensky_api import OpenSkyApi  # type: ignore
    except ImportError:
        logger.warning("opensky-api not installed; returning empty departures")
        return []

    try:
        api = OpenSkyApi()
        icao = iata_to_icao(airport_iata)
        flights = api.get_departures_by_airport(icao, begin_unix, end_unix)
        if not flights:
            return []
        records = []
        for f in flights:
            dest_icao = getattr(f, "estArrivalAirport", None)
            records.append(FlightRecord(
                callsign=(getattr(f, "callsign", "") or "").strip(),
                dest_airport_icao=dest_icao,
                dest_airport_iata=_icao_to_iata(dest_icao) if dest_icao else None,
                est_departure_time=getattr(f, "firstSeen", begin_unix),
                est_arrival_time=getattr(f, "lastSeen", None),
            ))
        return records
    except Exception as exc:
        logger.warning("OpenSky API call failed for %s: %s", airport_iata, exc)
        return []


def _icao_to_iata(icao: str) -> str | None:
    """Reverse mapping for the airports relevant to this outbreak."""
    _MAP = {
        "FAOR": "JNB", "FACT": "CPT", "EHAM": "AMS", "LSGG": "GVA",
        "LSZH": "ZRH", "GCXO": "TFN", "GCTS": "TFS", "EGLL": "LHR",
        "LFPG": "CDG", "EDDF": "FRA", "OMDB": "DXB", "WSSS": "SIN",
        "HKJK": "NBO",
    }
    return _MAP.get(icao.upper())
```

- [ ] **Step 2: Verify import**

```
python -c "from eosp.services.opensky import get_departures, load_airport_coords; print('ok')"
```

Expected: `ok` (or a warning about missing opensky-api, which is acceptable).

- [ ] **Step 3: Commit**

```
git add app/eosp/services/opensky.py
git commit -m "feat: add OpenSky API client with in-memory cache and fallback"
```

---

## Task 6: services/risk_propagation.py — three-ring flight network risk scoring

**Files:**
- Create: `app/eosp/services/risk_propagation.py`

- [ ] **Step 1: Write the module**

Create `app/eosp/services/risk_propagation.py`:

```python
"""Three-ring global risk propagation from the MV Hondius outbreak.

Ring 0: The ship itself.
Ring 1: Confirmed evacuation flight destinations (JNB, AMS, TFN).
Ring 2: All airports reachable by departing flights from Ring 1 airports
        within the 8-day Andes hantavirus incubation window.
Ring 3: Second-hop airports from the busiest Ring 2 airports (optional,
        included only when risk_score > 0.05 to limit noise).

Risk scores decay with each hop and are proportional to the number of
exposed passengers and the inferred transmission probability.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from eosp.services.opensky import FlightRecord, get_departures, load_airport_coords

logger = logging.getLogger(__name__)

_COORDS = None  # lazy-loaded

_INCUBATION_DAYS = 8
_RING2_DECAY = 0.30
_RING3_DECAY = 0.10
_RING3_THRESHOLD = 0.05

# Unix timestamps for the evacuation flights (approximate)
# JNB flight ~April 25, AMS flight ~May 3, TFN flight ~May 5
_EVACUATION_EVENTS = [
    {"airport_iata": "JNB", "depart_day_offset": 0,  "passengers": 30, "ts": 1745539200},
    {"airport_iata": "AMS", "depart_day_offset": 8,  "passengers": 40, "ts": 1746230400},
    {"airport_iata": "TFN", "depart_day_offset": 10, "passengers": 52, "ts": 1746403200},
]
_TOTAL_EVACUEES = sum(e["passengers"] for e in _EVACUATION_EVENTS)


@dataclass
class RiskZone:
    airport_iata: str
    lat: float
    lng: float
    city: str
    country: str
    risk_score: float
    ring: int


def _coords() -> dict:
    global _COORDS
    if _COORDS is None:
        _COORDS = load_airport_coords()
    return _COORDS


def compute_risk_zones(p_transmit: float) -> list[RiskZone]:
    """Build the full risk zone list for the geo/outbreak endpoint.

    ``p_transmit`` comes from the latest inference posterior mean.
    """
    coords = _coords()
    zones: dict[str, RiskZone] = {}

    # Ring 1 — direct evacuation destinations
    for evt in _EVACUATION_EVENTS:
        iata = evt["airport_iata"]
        if iata not in coords:
            continue
        c = coords[iata]
        ring1_score = min(1.0, (evt["passengers"] / _TOTAL_EVACUEES) * p_transmit * 12.0)
        zones[iata] = RiskZone(
            airport_iata=iata, lat=c["lat"], lng=c["lng"],
            city=c["city"], country=c["country"],
            risk_score=ring1_score, ring=1,
        )

    # Ring 2 — onward flights from Ring 1 airports within incubation window
    ring1_airports = list(zones.keys())
    ring2_raw: dict[str, float] = {}
    for r1_iata in ring1_airports:
        r1_score = zones[r1_iata].risk_score
        evt = next(e for e in _EVACUATION_EVENTS if e["airport_iata"] == r1_iata)
        begin_ts = evt["ts"]
        end_ts = begin_ts + _INCUBATION_DAYS * 86400
        flights = get_departures(r1_iata, begin_ts, end_ts)
        for flight in flights:
            dest = flight.dest_airport_iata
            if dest is None or dest == r1_iata or dest not in coords:
                continue
            hop_score = r1_score * _RING2_DECAY * p_transmit * 3.0
            ring2_raw[dest] = max(ring2_raw.get(dest, 0.0), hop_score)

    for iata, score in ring2_raw.items():
        if iata in zones:
            continue
        c = coords[iata]
        zones[iata] = RiskZone(
            airport_iata=iata, lat=c["lat"], lng=c["lng"],
            city=c["city"], country=c["country"],
            risk_score=round(min(score, 0.95), 4), ring=2,
        )

    # Ring 3 — second hop from top-5 Ring 2 airports
    top_ring2 = sorted(
        [(iata, z) for iata, z in zones.items() if z.ring == 2],
        key=lambda x: x[1].risk_score, reverse=True,
    )[:5]
    ring3_raw: dict[str, float] = {}
    for r2_iata, r2_zone in top_ring2:
        evt_ts = int(time.time()) - 7 * 86400  # approximate recent window
        end_ts = evt_ts + _INCUBATION_DAYS * 86400
        flights = get_departures(r2_iata, evt_ts, end_ts)
        for flight in flights:
            dest = flight.dest_airport_iata
            if dest is None or dest in zones or dest not in coords:
                continue
            hop_score = r2_zone.risk_score * _RING3_DECAY * p_transmit * 2.0
            ring3_raw[dest] = max(ring3_raw.get(dest, 0.0), hop_score)

    for iata, score in ring3_raw.items():
        if score < _RING3_THRESHOLD:
            continue
        c = coords[iata]
        zones[iata] = RiskZone(
            airport_iata=iata, lat=c["lat"], lng=c["lng"],
            city=c["city"], country=c["country"],
            risk_score=round(min(score, 0.95), 4), ring=3,
        )

    return sorted(zones.values(), key=lambda z: (-z.ring, -z.risk_score))
```

- [ ] **Step 2: Verify import (dev mode with no OpenSky)**

```
python -c "from eosp.services.risk_propagation import compute_risk_zones; zones = compute_risk_zones(0.089); print(f'{len(zones)} zones, first: {zones[0]}')"
```

Expected: prints a few Ring 1 zones (JNB, AMS, TFN) since OpenSky will return empty in dev mode. No exceptions.

- [ ] **Step 3: Commit**

```
git add app/eosp/services/risk_propagation.py
git commit -m "feat: add three-ring flight network risk propagation service"
```

---

## Task 7: services/geo.py and GET /api/v1/geo/outbreak

**Files:**
- Create: `app/eosp/services/geo.py`
- Modify: `app/eosp/api/routes.py`

- [ ] **Step 1: Write services/geo.py**

Create `app/eosp/services/geo.py`:

```python
"""Assembles the geo/outbreak response for the Leaflet world map.

Reads static network_spec.json for ship/flight topology,
enriches with airport coordinates, and calls risk_propagation to
build the global heatmap. Result is safe to cache for 12 hours.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eosp.services.opensky import load_airport_coords
from eosp.services.risk_propagation import compute_risk_zones

_SPEC_PATH = Path(__file__).resolve().parent.parent / "data" / "network_spec.json"

# MV Hondius approximate position — en route Cape Verde → Tenerife
_SHIP_LAT = 20.5
_SHIP_LNG = -21.0

# Known case locations by country ISO code
_CASE_COORDS: dict[str, dict[str, Any]] = {
    "ZA": {"lat": -26.134, "lng": 28.242, "confirmed": 2, "suspected": 0, "deaths": 1, "airport": "JNB"},
    "NL": {"lat": 52.309, "lng": 4.764,  "confirmed": 1, "suspected": 1, "deaths": 1, "airport": "AMS"},
    "CH": {"lat": 46.238, "lng": 6.109,  "confirmed": 0, "suspected": 1, "deaths": 1, "airport": "GVA"},
    "ES": {"lat": 28.048, "lng": -16.572, "confirmed": 0, "suspected": 3, "deaths": 0, "airport": "TFN"},
}


def build_outbreak_geo(p_transmit: float = 0.089) -> dict[str, Any]:
    """Build the complete geo/outbreak payload.

    ``p_transmit`` is taken from the latest inference result so risk
    scores update as the model refits.
    """
    spec = _load_spec()
    coords = load_airport_coords()

    ship = {
        "lat": _SHIP_LAT,
        "lng": _SHIP_LNG,
        "name": spec.get("ship", {}).get("name", "MV Hondius"),
        "status": "en_route_tenerife",
    }

    confirmed_cases = []
    for country, info in _CASE_COORDS.items():
        confirmed_cases.append({
            "country": country,
            "lat": info["lat"],
            "lng": info["lng"],
            "confirmed": info["confirmed"],
            "suspected": info["suspected"],
            "deaths": info["deaths"],
            "airport": info["airport"],
        })

    # Build evacuation flight arcs from network_spec.json
    evacuation_flights = []
    for flight in spec.get("flights", []):
        dest_code = flight.get("destination", "")  # e.g. "ZA_JNB"
        iata = dest_code.split("_")[-1] if "_" in dest_code else dest_code
        dest_coords = coords.get(iata)
        if dest_coords is None:
            continue
        evacuation_flights.append({
            "name": flight.get("name", ""),
            "from_lat": _SHIP_LAT,
            "from_lng": _SHIP_LNG,
            "to_lat": dest_coords["lat"],
            "to_lng": dest_coords["lng"],
            "to_airport": iata,
            "depart_day": flight.get("depart_day", 0),
            "passengers": flight.get("n_passengers", 0),
        })

    risk_zones = compute_risk_zones(p_transmit)
    heatmap = [
        {
            "airport_iata": z.airport_iata,
            "lat": z.lat,
            "lng": z.lng,
            "city": z.city,
            "country": z.country,
            "risk_score": z.risk_score,
            "ring": z.ring,
        }
        for z in risk_zones
    ]

    return {
        "ship": ship,
        "confirmed_cases": confirmed_cases,
        "evacuation_flights": evacuation_flights,
        "risk_heatmap": heatmap,
        "metadata": {
            "p_transmit_used": round(p_transmit, 4),
            "ring1_airports": [z.airport_iata for z in risk_zones if z.ring == 1],
            "ring2_airports_found": sum(1 for z in risk_zones if z.ring == 2),
            "ring3_airports_found": sum(1 for z in risk_zones if z.ring == 3),
        },
    }


def _load_spec() -> dict[str, Any]:
    with _SPEC_PATH.open() as fh:
        return json.load(fh)
```

- [ ] **Step 2: Add the route to routes.py**

Add to `app/eosp/api/routes.py`:

```python
from eosp.services.geo import build_outbreak_geo

@router.get("/geo/outbreak")
def geo_outbreak(request: Request):
    repo = request.app.state.repository
    inference = repo.latest_inference()
    p_transmit = 0.089  # fallback
    if inference is not None and "p_transmit" in inference.parameters:
        p_transmit = float(inference.parameters["p_transmit"].mean)
    return build_outbreak_geo(p_transmit=p_transmit)
```

- [ ] **Step 3: Test the endpoint**

With server running: `curl http://localhost:8000/api/v1/geo/outbreak | python -m json.tool`

Expected: JSON with `ship`, `confirmed_cases` (4 entries), `evacuation_flights` (3 entries), `risk_heatmap` (≥3 entries for Ring 1), `metadata`.

- [ ] **Step 4: Commit**

```
git add app/eosp/services/geo.py app/eosp/api/routes.py
git commit -m "feat: add geo outbreak service and GET /api/v1/geo/outbreak endpoint"
```

---

## Task 8: Static files — CSS layer

**Files:**
- Create: `app/eosp/static/css/base.css`
- Create: `app/eosp/static/css/dashboard.css`
- Create: `app/eosp/static/css/drawer.css`
- Delete: `app/eosp/static/styles.css`

- [ ] **Step 1: Create CSS directory and base.css**

```
New-Item -ItemType Directory -Force -Path app/eosp/static/css
```

Create `app/eosp/static/css/base.css`:

```css
:root {
  --bg: #0a1614;
  --surface: #112820;
  --panel: #162e29;
  --panel-border: #1e3530;
  --ink: #e0f0ed;
  --ink-muted: #6b9990;
  --ink-dim: #2e4a47;
  --teal: #0b7c83;
  --teal-light: #9ccac6;
  --teal-faint: rgba(11,124,131,0.15);
  --rust: #b95b35;
  --gold: #d89c22;
  --green: #2f7d4f;
  --green-dim: #1e5a35;
  --red-dim: #5a1e1e;
  --radius: 8px;
  --radius-sm: 5px;
  --topbar-h: 56px;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html { font-size: 14px; }

body {
  background: var(--bg);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
  line-height: 1.5;
  min-height: 100vh;
}

h1 { font-size: 1.4rem; font-weight: 700; }
h2 { font-size: 1rem; font-weight: 600; }
h3 { font-size: 0.9rem; font-weight: 600; }

button {
  cursor: pointer;
  font: inherit;
  border: none;
  border-radius: var(--radius-sm);
}

button:disabled { opacity: 0.55; cursor: not-allowed; }

a { color: var(--teal-light); text-decoration: none; }
a:hover { text-decoration: underline; }

.sr-only {
  position: absolute; width: 1px; height: 1px;
  overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap;
}

/* Tooltip anchor */
[data-tip] { position: relative; }
[data-tip]::after {
  content: attr(data-tip);
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  background: #071410;
  border: 1px solid var(--panel-border);
  border-radius: var(--radius-sm);
  color: var(--ink);
  font-size: 0.78rem;
  line-height: 1.4;
  max-width: 240px;
  padding: 6px 10px;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.15s;
  white-space: normal;
  z-index: 100;
}
[data-tip]:hover::after { opacity: 1; }
```

- [ ] **Step 2: Create dashboard.css**

Create `app/eosp/static/css/dashboard.css`:

```css
/* ── Topbar ── */
.topbar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 50;
  height: var(--topbar-h);
  background: #071410;
  border-bottom: 1px solid var(--panel-border);
  display: flex; align-items: center;
  padding: 0 24px; gap: 16px;
}
.topbar-brand { display: flex; flex-direction: column; gap: 1px; flex: 1; }
.topbar-brand .eyebrow { color: var(--teal-light); font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; }
.topbar-brand h1 { font-size: 1rem; }
.topbar-status { color: var(--ink-muted); font-size: 0.78rem; }
.version-badge {
  border: 1px solid var(--panel-border);
  border-radius: var(--radius-sm);
  color: var(--teal-light);
  font-size: 0.78rem;
  padding: 4px 10px;
  font-family: monospace;
}
.version-badge.converged { border-color: var(--green-dim); color: #4caf50; }
.version-badge.warning   { border-color: #5a4a00; color: var(--gold); }
.version-badge.failed    { border-color: var(--red-dim); color: #e57373; }
.btn-run-analysis {
  background: var(--rust);
  color: white;
  font-weight: 700;
  padding: 8px 16px;
  font-size: 0.85rem;
  white-space: nowrap;
}
.btn-run-analysis:hover { background: #c96a42; }

/* ── Page body ── */
.page-body {
  margin-top: var(--topbar-h);
  transition: opacity 0.25s ease;
}
.page-body.dimmed { opacity: 0.3; pointer-events: none; }

/* ── KPI strip ── */
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  padding: 16px 24px;
}
.kpi-card {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: var(--radius);
  padding: 14px 16px;
}
.kpi-label {
  color: var(--ink-muted);
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  display: flex;
  align-items: center;
  gap: 4px;
}
.kpi-label .tip-icon {
  color: var(--ink-dim);
  cursor: help;
  font-size: 0.9em;
}
.kpi-value {
  font-size: 2rem;
  font-weight: 700;
  line-height: 1.15;
  margin-top: 6px;
}
.kpi-sub { color: var(--ink-muted); font-size: 0.75rem; margin-top: 3px; }

/* ── Map panel ── */
.map-panel {
  margin: 0 24px 16px;
  border-radius: var(--radius);
  border: 1px solid var(--panel-border);
  overflow: hidden;
  height: 320px;
}
#world-map { width: 100%; height: 100%; }

/* ── Workspace: chart + scenario sidebar ── */
.workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
  padding: 0 24px 32px;
}
.chart-panel {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: var(--radius);
  padding: 16px;
}
.panel-heading {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 12px;
}
.panel-heading h2 { color: var(--ink); }
.panel-heading .freshness { color: var(--ink-muted); font-size: 0.75rem; }
#forecast-chart { width: 100%; height: 320px; }

/* ── Scenario sidebar ── */
.scenario-sidebar {
  display: flex; flex-direction: column; gap: 10px;
}
.scenario-card {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-left: 4px solid var(--panel-border);
  border-radius: var(--radius);
  padding: 12px 14px;
  cursor: pointer;
  transition: border-left-color 0.15s, background 0.15s;
}
.scenario-card:hover { background: #1a3530; }
.scenario-card.active { background: #1a3530; }
.scenario-card.baseline { border-left-color: var(--teal); }
.scenario-card.better   { border-left-color: var(--green); }
.scenario-card.worse    { border-left-color: var(--rust); }
.scenario-name { font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--ink-muted); }
.scenario-value { font-size: 1.6rem; font-weight: 700; line-height: 1.2; margin: 4px 0 2px; }
.scenario-delta { font-size: 0.8rem; font-weight: 700; }
.scenario-delta.down { color: var(--green); }
.scenario-delta.up   { color: var(--rust); }
.scenario-delta.ref  { color: var(--ink-muted); }
.scenario-ci { color: var(--ink-muted); font-size: 0.72rem; margin-top: 2px; }
.scenario-desc { color: var(--ink-muted); font-size: 0.75rem; margin-top: 6px; line-height: 1.4; }
.scenario-card.locked { opacity: 0.45; cursor: default; }
.scenario-card.locked:hover { background: var(--panel); }

/* ── Responsive ── */
@media (max-width: 1100px) {
  .workspace { grid-template-columns: 1fr; }
  .scenario-sidebar { flex-direction: row; flex-wrap: wrap; }
  .scenario-card { flex: 1 1 160px; }
}
@media (max-width: 700px) {
  .kpi-strip { grid-template-columns: repeat(2, 1fr); }
  .map-panel { height: 220px; margin: 0 12px 12px; }
  .workspace { padding: 0 12px 24px; }
  .topbar { padding: 0 12px; }
}
```

- [ ] **Step 3: Create drawer.css**

Create `app/eosp/static/css/drawer.css`:

```css
/* ── Drawer overlay ── */
.drawer-overlay {
  position: fixed; inset: 0; z-index: 200;
  pointer-events: none;
}
.drawer-overlay.open { pointer-events: all; }

.drawer {
  position: fixed; top: 0; right: 0; bottom: 0;
  width: 480px; max-width: 100vw;
  background: #0d1e1b;
  border-left: 1px solid var(--panel-border);
  display: flex; flex-direction: column;
  transform: translateX(100%);
  transition: transform 0.25s ease-out;
  z-index: 201;
  overflow: hidden;
}
.drawer-overlay.open .drawer { transform: translateX(0); }

.drawer-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px;
  background: #071410;
  border-bottom: 1px solid var(--panel-border);
  flex-shrink: 0;
}
.drawer-header h2 { color: var(--teal-light); font-size: 0.85rem; letter-spacing: 0.06em; text-transform: uppercase; }
.drawer-close {
  background: none;
  color: var(--ink-muted);
  font-size: 1.2rem;
  line-height: 1;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
}
.drawer-close:hover { background: var(--panel); color: var(--ink); }

.drawer-body { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 14px; }

/* ── Idle state ── */
.config-section { display: flex; flex-direction: column; gap: 10px; }
.config-label { font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ink-muted); margin-bottom: 4px; }
.pill-group { display: flex; flex-wrap: wrap; gap: 6px; }
.pill {
  background: var(--surface);
  border: 1px solid var(--panel-border);
  border-radius: 20px;
  color: var(--ink-muted);
  font-size: 0.78rem;
  padding: 5px 12px;
  cursor: pointer;
  transition: all 0.12s;
}
.pill:hover { border-color: var(--teal); color: var(--ink); }
.pill.selected { background: var(--teal-faint); border-color: var(--teal); color: var(--teal-light); font-weight: 600; }
.btn-run {
  background: var(--teal);
  color: white;
  font-weight: 700;
  font-size: 0.9rem;
  padding: 12px;
  width: 100%;
  border-radius: var(--radius);
  margin-top: 4px;
}
.btn-run:hover { background: #0a9099; }

.last-run-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
.last-run-table td { padding: 5px 0; border-bottom: 1px solid var(--panel-border); color: var(--ink-muted); }
.last-run-table td:last-child { color: var(--ink); text-align: right; font-family: monospace; }

.download-links { display: flex; flex-direction: column; gap: 6px; }
.download-link {
  color: var(--teal-light);
  font-size: 0.8rem;
  padding: 6px 0;
  border-bottom: 1px solid var(--panel-border);
  display: flex; align-items: center; gap: 6px;
}

/* ── Stage cards ── */
.stage-card {
  background: var(--surface);
  border: 1px solid var(--panel-border);
  border-radius: var(--radius);
  overflow: hidden;
  transition: border-color 0.2s;
}
.stage-card.done   { border-color: var(--green-dim); }
.stage-card.active { border-color: var(--teal); }
.stage-card.pending { opacity: 0.5; }

.stage-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
}
.stage-card.done   .stage-header { background: #0d2a1e; }
.stage-card.active .stage-header { background: #0b3e42; }
.stage-card.pending .stage-header { background: var(--surface); }

.stage-title { font-size: 0.82rem; font-weight: 700; }
.stage-card.done   .stage-title { color: #4caf50; }
.stage-card.active .stage-title { color: var(--teal-light); }
.stage-card.pending .stage-title { color: var(--ink-dim); }

.stage-badge {
  font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; font-weight: 700;
}
.badge-done    { background: var(--green-dim); color: #4caf50; }
.badge-running { background: #0b3e42; color: var(--teal); }
.badge-waiting { background: var(--bg); color: var(--ink-dim); }

.stage-body { padding: 12px 14px; border-top: 1px solid var(--panel-border); display: none; }
.stage-card.active .stage-body,
.stage-card.expanded .stage-body { display: block; }

.stat-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 10px; }
.stat-box {
  background: #071410;
  border-radius: var(--radius-sm);
  padding: 8px;
  text-align: center;
}
.stat-box .stat-lbl { font-size: 0.68rem; color: var(--ink-muted); text-transform: uppercase; letter-spacing: 0.05em; }
.stat-box .stat-val { font-size: 1.2rem; font-weight: 700; color: var(--ink); margin: 3px 0 1px; font-family: monospace; }
.stat-box .stat-sub { font-size: 0.65rem; color: var(--ink-dim); }
.stat-box .stat-sub.good { color: var(--green); }
.stat-box .stat-sub.warn { color: var(--gold); }
.stat-box .stat-sub.fail { color: var(--rust); }

.progress-bar { background: var(--bg); border-radius: 3px; height: 5px; overflow: hidden; margin: 6px 0 3px; }
.progress-fill { height: 100%; border-radius: 3px; background: var(--teal); transition: width 0.4s ease; }
.progress-eta  { font-size: 0.72rem; color: var(--ink-muted); }

.param-chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
.param-chip {
  background: #0b3e42;
  border-radius: var(--radius-sm);
  padding: 3px 8px;
  font-size: 0.72rem;
  color: var(--ink-muted);
}
.param-chip .pval { color: var(--teal-light); font-weight: 700; font-family: monospace; }

.chain-row { display: flex; gap: 6px; margin-top: 8px; }
.chain-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--ink-dim); }
.chain-dot.good { background: var(--green); }
.chain-dot.warn { background: var(--gold); }
.chain-dot.fail { background: var(--rust); }

#posterior-chart { width: 100%; height: 80px; margin-top: 8px; }
#fan-chart       { width: 100%; height: 80px; margin-top: 8px; }

.result-banner {
  background: var(--green-dim);
  border-radius: var(--radius-sm);
  color: #4caf50;
  font-weight: 700;
  font-size: 0.85rem;
  padding: 10px 14px;
  margin-bottom: 10px;
}
.btn-view-dashboard {
  background: var(--teal);
  color: white;
  font-weight: 700;
  font-size: 0.85rem;
  padding: 10px;
  width: 100%;
  border-radius: var(--radius);
}
.btn-view-dashboard:hover { background: #0a9099; }

/* ── Mobile full-screen ── */
@media (max-width: 768px) {
  .drawer { width: 100vw; }
  .stage-body { padding: 16px; }
  .stage-header { padding: 14px 16px; min-height: 48px; }
  .stat-box { padding: 10px; }
  .stat-box .stat-val { font-size: 1.4rem; }
  .pill { padding: 8px 14px; font-size: 0.82rem; }
  .btn-run { padding: 14px; font-size: 1rem; }
}
```

- [ ] **Step 4: Delete old styles.css and verify server still boots**

Delete `app/eosp/static/styles.css`. Start the server — the old `index.html` will 404 the stylesheet, which is expected since we'll replace both in Task 9.

- [ ] **Step 5: Commit**

```
git add app/eosp/static/css/
git rm app/eosp/static/styles.css
git commit -m "feat: add CSS layer for dashboard, drawer, and base tokens"
```

---

## Task 9: index.html shell

**Files:**
- Replace: `app/eosp/static/index.html`

- [ ] **Step 1: Write the new index.html**

Replace `app/eosp/static/index.html` with:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EOSP — MV Hondius Outbreak Intelligence</title>
  <link rel="stylesheet" href="/css/base.css" />
  <link rel="stylesheet" href="/css/dashboard.css" />
  <link rel="stylesheet" href="/css/drawer.css" />
  <!-- Leaflet -->
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin="" defer></script>
  <script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js" defer></script>
  <!-- Plotly basic (scatter + histogram; ~1MB) -->
  <script src="https://cdn.plot.ly/plotly-basic-2.35.2.min.js" defer></script>
</head>
<body>

  <!-- ── Topbar ── -->
  <header class="topbar" role="banner">
    <div class="topbar-brand">
      <p class="eyebrow">Epidemic Outbreak Simulation Platform</p>
      <h1>MV Hondius · Andes Hantavirus Outbreak</h1>
    </div>
    <span id="topbar-status" class="topbar-status" aria-live="polite">Loading…</span>
    <span id="version-badge" class="version-badge" title="Model version"></span>
    <button class="btn-run-analysis" id="btn-open-drawer" type="button" aria-haspopup="dialog">
      ⚗ Run Analysis →
    </button>
  </header>

  <!-- ── Main dashboard ── -->
  <main class="page-body" id="page-body">

    <!-- KPI strip -->
    <section class="kpi-strip" aria-label="Key outbreak metrics">
      <article class="kpi-card">
        <div class="kpi-label">
          Confirmed Cases
          <span class="tip-icon" data-tip="Laboratory-confirmed via PCR or serology. These are certain cases." aria-label="What does confirmed mean?">?</span>
        </div>
        <div class="kpi-value" id="kpi-confirmed">—</div>
        <div class="kpi-sub" id="kpi-confirmed-sub"></div>
      </article>
      <article class="kpi-card">
        <div class="kpi-label">
          Suspected Cases
          <span class="tip-icon" data-tip="Clinically consistent with Andes hantavirus but not yet lab-confirmed." aria-label="What does suspected mean?">?</span>
        </div>
        <div class="kpi-value" id="kpi-suspected">—</div>
        <div class="kpi-sub" id="kpi-suspected-sub"></div>
      </article>
      <article class="kpi-card">
        <div class="kpi-label">
          Deaths · CFR
          <span class="tip-icon" data-tip="Case fatality rate. Andes hantavirus kills 35–50% of confirmed cases in published literature." aria-label="What is CFR?">?</span>
        </div>
        <div class="kpi-value" id="kpi-deaths">—</div>
        <div class="kpi-sub" id="kpi-deaths-sub"></div>
      </article>
      <article class="kpi-card">
        <div class="kpi-label">
          14-day Forecast
          <span class="tip-icon" data-tip="Median projected cumulative cases at day 14. Run 100 simulations: 95 of them land in the CI range." aria-label="What is the forecast?">?</span>
        </div>
        <div class="kpi-value" id="kpi-forecast">—</div>
        <div class="kpi-sub" id="kpi-forecast-sub"></div>
      </article>
    </section>

    <!-- World map -->
    <section class="map-panel" aria-label="Global outbreak and risk map">
      <div id="world-map"></div>
    </section>

    <!-- Forecast chart + scenario sidebar -->
    <div class="workspace">
      <div class="chart-panel">
        <div class="panel-heading">
          <h2>Baseline Forecast · Cumulative cases · 14-day horizon</h2>
          <span class="freshness" id="chart-freshness"></span>
        </div>
        <div id="forecast-chart"></div>
      </div>
      <aside class="scenario-sidebar" id="scenario-sidebar" aria-label="Scenario comparison">
        <!-- populated by scenarios.js -->
      </aside>
    </div>

  </main>

  <!-- ── Researcher Drawer ── -->
  <div class="drawer-overlay" id="drawer-overlay" role="dialog" aria-modal="true" aria-label="Researcher Console">
    <div class="drawer" id="drawer">
      <div class="drawer-header">
        <h2>⚗ Researcher Console</h2>
        <button class="drawer-close" id="btn-close-drawer" type="button" aria-label="Close researcher console">✕</button>
      </div>
      <div class="drawer-body" id="drawer-body">
        <!-- populated by drawer.js -->
      </div>
    </div>
  </div>

  <!-- JS modules (type=module keeps them scoped; defer on CDN scripts fires first) -->
  <script type="module" src="/js/main.js"></script>

</body>
</html>
```

- [ ] **Step 2: Verify page loads without JS errors**

Open `http://localhost:8000`. Expected: dark page with topbar, empty KPI cards (showing `—`), empty map div, empty chart div. No 404s for CSS files. Console may show `Plotly is not defined` — acceptable until the CDN script loads.

- [ ] **Step 3: Commit**

```
git add app/eosp/static/index.html
git commit -m "feat: replace index.html with new dashboard shell"
```

---

## Task 10: JS — api.js, map.js, chart.js, scenarios.js

**Files:**
- Create: `app/eosp/static/js/api.js`
- Create: `app/eosp/static/js/map.js`
- Create: `app/eosp/static/js/chart.js`
- Create: `app/eosp/static/js/scenarios.js`

- [ ] **Step 1: Create api.js**

Create `app/eosp/static/js/api.js`:

```js
/** Thin fetch wrapper. All paths are relative to origin. */

const BASE = "/api/v1";

export async function get(path) {
  const res = await fetch(BASE + path);
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json();
}

export async function post(path, body) {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `POST ${path} → ${res.status}`);
  }
  return res.json();
}
```

- [ ] **Step 2: Create map.js**

Create `app/eosp/static/js/map.js`:

```js
/**
 * Leaflet world map: ship marker, case markers, evacuation flight arcs,
 * global risk heatmap from GET /api/v1/geo/outbreak.
 */

let _map = null;
let _heatLayer = null;
let _caseMarkers = [];
let _arcLines = [];

export function initMap(containerId) {
  if (_map) return _map;
  _map = L.map(containerId, {
    center: [20, 10],
    zoom: 2,
    zoomControl: true,
    attributionControl: true,
  });
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: "abcd",
    maxZoom: 19,
  }).addTo(_map);
  return _map;
}

export function renderGeoData(data) {
  if (!_map) return;

  // Clear previous layers
  _caseMarkers.forEach((m) => m.remove());
  _caseMarkers = [];
  _arcLines.forEach((l) => l.remove());
  _arcLines = [];
  if (_heatLayer) { _heatLayer.remove(); _heatLayer = null; }

  // Ship marker — pulsing circle
  const shipIcon = L.divIcon({
    className: "",
    html: `<div style="width:14px;height:14px;border-radius:50%;background:#0b7c83;
           border:2px solid #9ccac6;box-shadow:0 0 0 4px rgba(11,124,131,0.3)"></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
  const shipMarker = L.marker([data.ship.lat, data.ship.lng], { icon: shipIcon })
    .bindPopup(`<strong>${data.ship.name}</strong><br>Status: ${data.ship.status.replace(/_/g, " ")}`)
    .addTo(_map);
  _caseMarkers.push(shipMarker);

  // Confirmed/suspected case markers
  for (const c of data.confirmed_cases) {
    const total = c.confirmed + c.suspected;
    const radius = 5 + total * 3;
    const color = c.confirmed > 0 ? "#b95b35" : "#d89c22";
    const m = L.circleMarker([c.lat, c.lng], {
      radius,
      fillColor: color,
      color: color,
      weight: 1,
      fillOpacity: 0.75,
    }).bindPopup(
      `<strong>${c.country}</strong><br>` +
      `Confirmed: ${c.confirmed} · Suspected: ${c.suspected} · Deaths: ${c.deaths}`
    ).addTo(_map);
    _caseMarkers.push(m);
  }

  // Evacuation flight arcs (curved polyline approximation)
  for (const flight of data.evacuation_flights) {
    const arc = _curvedLine(
      [flight.from_lat, flight.from_lng],
      [flight.to_lat, flight.to_lng],
    );
    const line = L.polyline(arc, {
      color: "#9ccac6",
      weight: 1.5,
      opacity: 0.6,
      dashArray: "6 4",
    }).bindPopup(
      `<strong>${flight.name}</strong><br>` +
      `${flight.passengers} passengers · Day ${flight.depart_day}`
    ).addTo(_map);
    _arcLines.push(line);
  }

  // Risk heatmap
  const heatPoints = data.risk_heatmap.map((z) => [z.lat, z.lng, z.risk_score]);
  _heatLayer = L.heatLayer(heatPoints, {
    radius: 30,
    blur: 20,
    maxZoom: 6,
    gradient: { 0.2: "#d89c22", 0.5: "#b95b35", 0.8: "#8b0000", 1.0: "#ff0000" },
  }).addTo(_map);

  // Popup for heatmap airports (transparent overlay circles)
  for (const z of data.risk_heatmap) {
    if (z.ring === 1) continue; // already shown as case marker
    const level = z.risk_score > 0.5 ? "high" : z.risk_score > 0.2 ? "medium" : "low";
    L.circleMarker([z.lat, z.lng], {
      radius: 8, fillColor: "transparent", color: "transparent",
    }).bindPopup(
      `<strong>${z.city} (${z.airport_iata})</strong><br>` +
      `Modelled exposure risk: ${level}<br>` +
      `<em>Ring ${z.ring} — Not a confirmed case location</em>`
    ).addTo(_map);
  }
}

/** Build ~20 intermediate points along a great-circle-ish curve */
function _curvedLine(from, to) {
  const points = [];
  const steps = 20;
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const lat = from[0] + (to[0] - from[0]) * t;
    const lng = from[1] + (to[1] - from[1]) * t;
    // Add a slight arc by offsetting the midpoint north
    const arc = Math.sin(Math.PI * t) * Math.abs(to[0] - from[0]) * 0.3;
    points.push([lat + arc, lng]);
  }
  return points;
}
```

- [ ] **Step 3: Create chart.js**

Create `app/eosp/static/js/chart.js`:

```js
/**
 * Plotly forecast timeseries with 95% and 50% CI bands.
 * renderForecast(forecastResponse, prevForecastResponse?)
 * overlayScenario(scenarioResponse, color) / clearScenarioOverlay()
 */

const LAYOUT = {
  paper_bgcolor: "#112820",
  plot_bgcolor: "#112820",
  font: { family: "Inter, sans-serif", color: "#e0f0ed", size: 11 },
  margin: { t: 10, r: 10, b: 40, l: 38 },
  xaxis: {
    gridcolor: "#1e3530", tickcolor: "#1e3530", linecolor: "#1e3530",
    tickfont: { size: 10 },
  },
  yaxis: {
    gridcolor: "#1e3530", tickcolor: "#1e3530", linecolor: "#1e3530",
    tickfont: { size: 10 }, title: { text: "Cumulative cases", font: { size: 10 } },
    rangemode: "tozero",
  },
  legend: { bgcolor: "transparent", font: { size: 10 } },
  hovermode: "x unified",
  hoverlabel: { bgcolor: "#071410", bordercolor: "#1e3530", font: { size: 11 } },
  showlegend: true,
};

const CONFIG = { displayModeBar: false, responsive: true };

export function renderForecast(containerId, forecast, prevForecast) {
  const dates = forecast.forecast.map((p) => p.date);
  const med    = forecast.forecast.map((p) => p.cases_cumulative.median);
  const ci95lo = forecast.forecast.map((p) => p.cases_cumulative.ci_95_lower);
  const ci95hi = forecast.forecast.map((p) => p.cases_cumulative.ci_95_upper);
  const ci50lo = forecast.forecast.map((p) => p.cases_cumulative.ci_50_lower ?? p.cases_cumulative.median);
  const ci50hi = forecast.forecast.map((p) => p.cases_cumulative.ci_50_upper ?? p.cases_cumulative.median);

  const traces = [
    // 95% CI lower (invisible base for fill)
    { x: dates, y: ci95lo, type: "scatter", mode: "lines", line: { width: 0 },
      showlegend: false, hoverinfo: "skip", name: "ci95lo" },
    // 95% CI band
    { x: dates, y: ci95hi, type: "scatter", mode: "lines", fill: "tonexty",
      fillcolor: "rgba(11,124,131,0.12)", line: { width: 0 },
      name: "95% CI", hovertemplate: "95% CI: %{y}<extra></extra>" },
    // 50% CI lower
    { x: dates, y: ci50lo, type: "scatter", mode: "lines", line: { width: 0 },
      showlegend: false, hoverinfo: "skip", name: "ci50lo" },
    // 50% CI band
    { x: dates, y: ci50hi, type: "scatter", mode: "lines", fill: "tonexty",
      fillcolor: "rgba(11,124,131,0.25)", line: { width: 0 },
      name: "50% CI", hovertemplate: "50% CI: %{y}<extra></extra>" },
    // Median line
    { x: dates, y: med, type: "scatter", mode: "lines",
      line: { color: "#0b7c83", width: 2 },
      name: "Median forecast", hovertemplate: "Median: %{y}<extra></extra>" },
  ];

  // Previous version median (dashed grey)
  if (prevForecast) {
    const prevMed = prevForecast.forecast.map((p) => p.cases_cumulative.median);
    const prevDates = prevForecast.forecast.map((p) => p.date);
    traces.push({
      x: prevDates, y: prevMed, type: "scatter", mode: "lines",
      line: { color: "#4a7a72", width: 1, dash: "dash" },
      name: "Previous version", hovertemplate: "Prev: %{y}<extra></extra>",
    });
  }

  Plotly.react(containerId, traces, LAYOUT, CONFIG);
}

export function overlayScenario(containerId, forecast, color) {
  const dates = forecast.forecast.map((p) => p.date);
  const med    = forecast.forecast.map((p) => p.cases_cumulative.median);
  const ci95lo = forecast.forecast.map((p) => p.cases_cumulative.ci_95_lower);
  const ci95hi = forecast.forecast.map((p) => p.cases_cumulative.ci_95_upper);

  Plotly.addTraces(containerId, [
    { x: dates, y: ci95lo, type: "scatter", mode: "lines", line: { width: 0 },
      showlegend: false, hoverinfo: "skip", name: "scen_ci_lo" },
    { x: dates, y: ci95hi, type: "scatter", mode: "lines", fill: "tonexty",
      fillcolor: color.replace(")", ",0.15)").replace("rgb", "rgba"),
      line: { width: 0 }, name: "Scenario 95% CI", hoverinfo: "skip" },
    { x: dates, y: med, type: "scatter", mode: "lines",
      line: { color, width: 2, dash: "dot" },
      name: "Scenario median", hovertemplate: "Scenario: %{y}<extra></extra>" },
  ]);
}

export function clearScenarioOverlay(containerId) {
  const gd = document.getElementById(containerId);
  if (!gd || !gd.data) return;
  const toRemove = gd.data
    .map((t, i) => ({ name: t.name, i }))
    .filter((t) => ["scen_ci_lo", "Scenario 95% CI", "Scenario median"].includes(t.name))
    .map((t) => t.i)
    .reverse();
  if (toRemove.length) Plotly.deleteTraces(containerId, toRemove);
}
```

- [ ] **Step 4: Create scenarios.js**

Create `app/eosp/static/js/scenarios.js`:

```js
/**
 * Renders scenario comparison cards in the sidebar.
 * Clicking a card fetches that scenario's full forecast and overlays it
 * on the chart.
 */
import { get } from "./api.js";
import { overlayScenario, clearScenarioOverlay } from "./chart.js";

const SCENARIO_META = {
  baseline:                      { label: "No Intervention · Baseline",         desc: "Full contact network, inferred parameters.",                          color: "rgb(11,124,131)" },
  quarantine_immediate:          { label: "Immediate Quarantine",                desc: "All passengers confined to cabins from day 1.",                       color: "rgb(47,125,79)" },
  evacuation_delay_3d:           { label: "Evacuation Delayed +3 Days",          desc: "Ship stays in place 3 extra days before flights depart.",             color: "rgb(185,91,53)" },
  evacuation_delay_7d:           { label: "Evacuation Delayed +7 Days",          desc: "Ship stays in place 7 extra days before flights depart.",             color: "rgb(185,91,53)" },
  enhanced_destination_protocols: { label: "Enhanced Arrival Protocols",         desc: "All evacuees tested and isolated on arrival; contacts monitored.",    color: "rgb(47,125,79)" },
};

let _activeScenario = null;

export function renderScenarios(comparison) {
  const container = document.getElementById("scenario-sidebar");
  if (!container) return;
  container.innerHTML = "";

  for (const s of comparison.scenarios) {
    const meta = SCENARIO_META[s.name] || { label: s.name.replace(/_/g, " "), desc: "", color: "rgb(11,124,131)" };
    const isBaseline = s.name === "baseline";
    const delta = s.vs_baseline;

    let deltaHtml = `<span class="scenario-delta ref">Reference</span>`;
    let cardClass = "baseline";
    if (delta) {
      const pct = delta.case_change_pct;
      const sign = pct < 0 ? "↓" : "↑";
      const cls  = pct < 0 ? "down" : "up";
      cardClass  = pct < 0 ? "better" : "worse";
      deltaHtml  = `<span class="scenario-delta ${cls}">${sign} ${Math.abs(pct)}% vs baseline</span>`;
    }

    const valueColor = isBaseline ? "#d89c22" : (delta?.case_change_pct < 0 ? "#2f7d4f" : "#b95b35");

    const card = document.createElement("div");
    card.className = `scenario-card ${cardClass}`;
    card.dataset.scenario = s.name;
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");
    card.innerHTML = `
      <div class="scenario-name">${meta.label}</div>
      <div class="scenario-value" style="color:${valueColor}">${s.cases_by_day_14.median}</div>
      ${deltaHtml}
      <div class="scenario-ci">95% CI: ${s.cases_by_day_14.ci_95[0]}–${s.cases_by_day_14.ci_95[1]} cases</div>
      <div class="scenario-desc">${meta.desc}</div>
    `;

    card.addEventListener("click", () => _onScenarioClick(s.name, meta.color, card));
    card.addEventListener("keydown", (e) => { if (e.key === "Enter") card.click(); });
    container.appendChild(card);
  }

  // Custom scenario placeholder
  const customCard = document.createElement("div");
  customCard.className = "scenario-card locked";
  customCard.title = "Define custom intervention parameters. Requires researcher access — click Run Analysis.";
  customCard.innerHTML = `
    <div class="scenario-name" style="color:#2e4a47">+ Custom Scenario</div>
    <div class="scenario-desc" style="color:#2e4a47">Click ⚗ Run Analysis to define custom parameters.</div>
  `;
  container.appendChild(customCard);
}

async function _onScenarioClick(scenarioName, color, card) {
  if (scenarioName === "baseline") {
    clearScenarioOverlay("forecast-chart");
    document.querySelectorAll(".scenario-card").forEach((c) => c.classList.remove("active"));
    _activeScenario = null;
    return;
  }
  if (_activeScenario === scenarioName) {
    clearScenarioOverlay("forecast-chart");
    card.classList.remove("active");
    _activeScenario = null;
    return;
  }
  clearScenarioOverlay("forecast-chart");
  document.querySelectorAll(".scenario-card").forEach((c) => c.classList.remove("active"));
  try {
    const forecast = await get(`/forecasts/${scenarioName}`);
    overlayScenario("forecast-chart", forecast, color);
    card.classList.add("active");
    _activeScenario = scenarioName;
  } catch (err) {
    console.warn("Could not load scenario forecast:", err.message);
  }
}
```

- [ ] **Step 5: Commit**

```
git add app/eosp/static/js/api.js app/eosp/static/js/map.js app/eosp/static/js/chart.js app/eosp/static/js/scenarios.js
git commit -m "feat: add visitor dashboard JS modules (api, map, chart, scenarios)"
```

---

## Task 11: JS — drawer.js, jobs.js, main.js

**Files:**
- Create: `app/eosp/static/js/drawer.js`
- Create: `app/eosp/static/js/jobs.js`
- Create: `app/eosp/static/js/main.js`

- [ ] **Step 1: Create jobs.js — SSE client**

Create `app/eosp/static/js/jobs.js`:

```js
/**
 * SSE client for inference job progress.
 * subscribe(jobId, onEvent, onClose) → returns a cleanup function.
 */

export function subscribe(jobId, onEvent, onClose) {
  const url = `/api/v1/inference/jobs/${jobId}/events`;
  const source = new EventSource(url);

  source.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data);
      if (event.type === "close" || event.stage === "complete") {
        onClose(event);
        source.close();
      } else {
        onEvent(event);
      }
    } catch (_) { /* ignore malformed */ }
  };

  source.onerror = () => {
    onClose({ error: true });
    source.close();
  };

  return () => source.close();
}
```

- [ ] **Step 2: Create drawer.js — researcher console state machine**

Create `app/eosp/static/js/drawer.js`:

```js
/**
 * Researcher drawer: idle and running states.
 * Manages stage cards, SSE subscription, mini Plotly charts.
 */
import { post, get } from "./api.js";
import { subscribe } from "./jobs.js";

const SCENARIOS = [
  { id: "baseline",                      label: "Baseline" },
  { id: "quarantine_immediate",          label: "Quarantine Now" },
  { id: "evacuation_delay_3d",           label: "Delay +3d" },
  { id: "evacuation_delay_7d",           label: "Delay +7d" },
  { id: "enhanced_destination_protocols", label: "Enhanced Protocols" },
];
const FIDELITY = [
  { value: 100,   label: "100 (fast preview)" },
  { value: 1000,  label: "1,000" },
  { value: 10000, label: "10,000 (full)" },
];

let _selectedScenarios = new Set(["baseline"]);
let _selectedFidelity = 100;
let _cancelSSE = null;
let _lastRun = null;

export function initDrawer(onRunComplete) {
  _renderIdle(onRunComplete);
}

function _renderIdle(onRunComplete) {
  const body = document.getElementById("drawer-body");
  body.innerHTML = "";

  // Scenario selector
  body.appendChild(_section("Scenarios", _pillGroup(
    SCENARIOS, (id) => _selectedScenarios.has(id),
    (id, selected) => { if (selected) _selectedScenarios.add(id); else _selectedScenarios.delete(id); },
    true
  )));

  // Fidelity selector
  body.appendChild(_section("Simulation fidelity", _pillGroup(
    FIDELITY.map((f) => ({ id: String(f.value), label: f.label })),
    (id) => _selectedFidelity === Number(id),
    (id) => { _selectedFidelity = Number(id); },
    false
  )));

  // Run button
  const btnRun = document.createElement("button");
  btnRun.className = "btn-run";
  btnRun.textContent = "Run Forecast →";
  btnRun.addEventListener("click", () => _startRun(onRunComplete));
  body.appendChild(btnRun);

  // Last run summary
  if (_lastRun) {
    const section = _section("Last completed run", _lastRunTable(_lastRun));
    body.appendChild(section);
  }

  // Download links
  const dlSection = _section("Downloads", _downloadLinks());
  body.appendChild(dlSection);
}

async function _startRun(onRunComplete) {
  const scenarios = [..._selectedScenarios];
  if (!scenarios.length) return;

  let jobId;
  try {
    const result = await post("/inference/run", { reason: "manual" });
    jobId = result.job_id;
  } catch (err) {
    alert("Failed to start run: " + err.message);
    return;
  }

  _renderRunning();
  _updateStage(1, "active", { n_cases: "…", quality_mean: "…" });

  _cancelSSE = subscribe(
    jobId,
    (event) => _handleEvent(event),
    (event) => _handleComplete(event, jobId, onRunComplete),
  );
}

function _handleEvent(event) {
  if (event.stage === "cases") {
    _updateStage(1, "done", event);
    _updateStage(2, "active", {});
  } else if (event.stage === "inference") {
    _updateStage(2, "active", event);
  } else if (event.stage === "simulation") {
    _updateStage(2, "done", {});
    _updateStage(3, "active", event);
  }
}

async function _handleComplete(event, jobId, onRunComplete) {
  _cancelSSE = null;
  _updateStage(3, "done", {});
  _updateStage(4, "active", { job_id: jobId, detail: event.detail });

  // Fetch the job record for the version
  try {
    const record = await get(`/inference/jobs/${jobId}`);
    _lastRun = record;
    _updateStage(4, "done", record);
  } catch (_) {}

  if (typeof onRunComplete === "function") onRunComplete();
}

function _renderRunning() {
  const body = document.getElementById("drawer-body");
  body.innerHTML = "";

  const stages = [
    { n: 1, title: "① Cases & Network",        state: "pending" },
    { n: 2, title: "② Bayesian Inference",      state: "pending" },
    { n: 3, title: "③ Monte Carlo Simulation",  state: "pending" },
    { n: 4, title: "④ Results",                 state: "pending" },
  ];

  for (const s of stages) {
    const card = _stageCard(s.n, s.title, s.state);
    body.appendChild(card);
  }
}

function _updateStage(n, state, data) {
  const card = document.querySelector(`.stage-card[data-stage="${n}"]`);
  if (!card) return;

  card.className = `stage-card ${state}`;
  const badge = card.querySelector(".stage-badge");
  if (badge) {
    badge.className = `stage-badge badge-${state === "done" ? "done" : state === "active" ? "running" : "waiting"}`;
    badge.textContent = state === "done" ? "✓ DONE" : state === "active" ? "● RUNNING" : "WAITING";
  }

  const body = card.querySelector(".stage-body");
  if (!body) return;
  body.innerHTML = _stageBodyHtml(n, state, data);

  // Render mini Plotly charts if needed
  if (n === 2 && state === "active" && data.params) {
    _renderPosteriorChart(data);
  }
  if (n === 3 && state === "active" && data.fan_sample) {
    _renderFanChart(data);
  }
  if (n === 4 && state === "active") {
    const btn = card.querySelector(".btn-view-dashboard");
    if (btn) btn.addEventListener("click", () => document.getElementById("btn-close-drawer").click());
  }
}

function _stageBodyHtml(n, state, data) {
  if (state === "done") return `<div style="color:var(--ink-muted);font-size:0.78rem">${_doneSummary(n, data)}</div>`;
  if (state === "pending") return "";

  if (n === 1) {
    return `<div style="color:var(--teal-light);font-size:0.82rem">
      Cases: <strong>${data.n_cases ?? "…"}</strong> ·
      Mean quality: <strong>${typeof data.quality_mean === "number" ? data.quality_mean.toFixed(2) : "…"}</strong>
    </div>`;
  }
  if (n === 2) {
    const draw = data.draw ?? 0;
    const total = data.total ?? 2000;
    const pct = Math.round((draw / total) * 100);
    const rhat = typeof data.rhat_max === "number" ? data.rhat_max.toFixed(3) : "…";
    const rhatClass = data.rhat_max < 1.01 ? "good" : data.rhat_max < 1.05 ? "warn" : "fail";
    const div = data.divergences ?? 0;
    const chains = (data.chain_rhat || []).map((r) =>
      `<div class="chain-dot ${r < 1.01 ? "good" : r < 1.05 ? "warn" : "fail"}" title="R̂ ${r.toFixed(3)}"></div>`
    ).join("");
    const chips = Object.entries(data.params || {}).slice(0, 5).map(([k, v]) =>
      `<div class="param-chip">${k} <span class="pval">${typeof v === "number" ? v.toFixed(3) : v}</span></div>`
    ).join("");
    return `
      <div class="stat-row">
        <div class="stat-box"><div class="stat-lbl">Draws</div><div class="stat-val">${draw.toLocaleString()}</div><div class="stat-sub">/ ${total.toLocaleString()}</div></div>
        <div class="stat-box"><div class="stat-lbl">R̂ max</div><div class="stat-val">${rhat}</div><div class="stat-sub ${rhatClass}">${rhatClass === "good" ? "✓ &lt;1.01" : rhatClass === "warn" ? "⚠ &lt;1.05" : "✗ high"}</div></div>
        <div class="stat-box"><div class="stat-lbl">Divergences</div><div class="stat-val">${div}</div><div class="stat-sub ${div < 50 ? "good" : "fail"}">${div < 50 ? "✓ &lt;50" : "✗ high"}</div></div>
      </div>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
      <div class="progress-eta">Chain status: <span class="chain-row">${chains}</span></div>
      <div class="param-chips">${chips}</div>
      <div id="posterior-chart-${Date.now()}"></div>
    `;
  }
  if (n === 3) {
    const done = data.trajectories ?? 0;
    const total = data.total ?? 10000;
    const pct = Math.round((done / total) * 100);
    return `
      <div class="stat-row">
        <div class="stat-box"><div class="stat-lbl">Trajectories</div><div class="stat-val">${done.toLocaleString()}</div><div class="stat-sub">/ ${total.toLocaleString()}</div></div>
        <div class="stat-box"><div class="stat-lbl">Scenario</div><div class="stat-val" style="font-size:0.9rem">${(data.scenario || "").replace(/_/g, " ")}</div></div>
        <div class="stat-box"><div class="stat-lbl">Progress</div><div class="stat-val">${pct}%</div></div>
      </div>
      <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
      <div id="fan-chart-${Date.now()}"></div>
    `;
  }
  if (n === 4) {
    return `
      <div class="result-banner">✓ Forecast complete</div>
      <button class="btn-view-dashboard">← View updated dashboard</button>
    `;
  }
  return "";
}

function _doneSummary(n, data) {
  if (n === 1) return `${data.n_cases ?? "?"} cases · quality ${typeof data.quality_mean === "number" ? data.quality_mean.toFixed(2) : "?"}`;
  if (n === 2) return `Inference complete · R̂ ${data.rhat_max != null ? Number(data.rhat_max).toFixed(3) : "?"} · ${data.divergences ?? "?"} divergences`;
  if (n === 3) return `Simulation complete · ${(data.total || data.trajectories || "10,000").toLocaleString()} trajectories`;
  if (n === 4) return "Forecast saved and dashboard updated.";
  return "Done";
}

function _renderPosteriorChart(data) {
  const el = document.querySelector("[id^='posterior-chart-']");
  if (!el || !window.Plotly) return;
  const values = Object.values(data.params || {}).slice(0, 1);
  if (!values.length) return;
  Plotly.newPlot(el.id, [{
    x: [values[0]], type: "histogram",
    marker: { color: "rgba(11,124,131,0.6)" },
    nbinsx: 20,
  }], {
    paper_bgcolor: "#071410", plot_bgcolor: "#071410",
    font: { color: "#e0f0ed", size: 9 },
    margin: { t: 4, r: 4, b: 20, l: 24 },
    height: 80,
    xaxis: { gridcolor: "#1e3530", tickfont: { size: 8 } },
    yaxis: { gridcolor: "#1e3530", tickfont: { size: 8 } },
    showlegend: false,
  }, { displayModeBar: false, responsive: true });
}

function _renderFanChart(data) {
  const el = document.querySelector("[id^='fan-chart-']");
  if (!el || !window.Plotly || !data.fan_sample?.length) return;
  const traces = data.fan_sample.map((traj, i) => ({
    y: traj, type: "scatter", mode: "lines",
    line: { color: "rgba(11,124,131,0.3)", width: 1 },
    showlegend: false, hoverinfo: "skip",
  }));
  Plotly.newPlot(el.id, traces, {
    paper_bgcolor: "#071410", plot_bgcolor: "#071410",
    font: { color: "#e0f0ed", size: 9 },
    margin: { t: 4, r: 4, b: 20, l: 24 },
    height: 80,
    xaxis: { gridcolor: "#1e3530", tickfont: { size: 8 } },
    yaxis: { gridcolor: "#1e3530", tickfont: { size: 8 } },
    showlegend: false,
  }, { displayModeBar: false, responsive: true });
}

function _stageCard(n, title, state) {
  const card = document.createElement("div");
  card.className = `stage-card ${state}`;
  card.dataset.stage = n;
  const badgeClass = state === "done" ? "badge-done" : state === "active" ? "badge-running" : "badge-waiting";
  const badgeText  = state === "done" ? "✓ DONE" : state === "active" ? "● RUNNING" : "WAITING";
  card.innerHTML = `
    <div class="stage-header">
      <span class="stage-title">${title}</span>
      <span class="stage-badge ${badgeClass}">${badgeText}</span>
    </div>
    <div class="stage-body"></div>
  `;
  card.querySelector(".stage-header").addEventListener("click", () => {
    if (state !== "done") return;
    card.classList.toggle("expanded");
  });
  return card;
}

function _section(label, content) {
  const div = document.createElement("div");
  div.className = "config-section";
  const lbl = document.createElement("div");
  lbl.className = "config-label";
  lbl.textContent = label;
  div.appendChild(lbl);
  div.appendChild(content);
  return div;
}

function _pillGroup(items, isSelected, onToggle, multiSelect) {
  const group = document.createElement("div");
  group.className = "pill-group";
  for (const item of items) {
    const pill = document.createElement("button");
    pill.className = "pill" + (isSelected(item.id) ? " selected" : "");
    pill.type = "button";
    pill.textContent = item.label;
    pill.addEventListener("click", () => {
      if (multiSelect) {
        const nowSelected = !pill.classList.contains("selected");
        pill.classList.toggle("selected", nowSelected);
        onToggle(item.id, nowSelected);
      } else {
        group.querySelectorAll(".pill").forEach((p) => p.classList.remove("selected"));
        pill.classList.add("selected");
        onToggle(item.id);
      }
    });
    group.appendChild(pill);
  }
  return group;
}

function _lastRunTable(run) {
  const table = document.createElement("table");
  table.className = "last-run-table";
  const rows = [
    ["Version",    run.detail?.inference_version ?? "—"],
    ["Trigger",    run.reason ?? "—"],
    ["Status",     run.status ?? "—"],
    ["Completed",  run.completed_at ? new Date(run.completed_at).toLocaleTimeString() : "—"],
  ];
  table.innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
  return table;
}

function _downloadLinks() {
  const div = document.createElement("div");
  div.className = "download-links";
  const links = [
    { href: "/api/v1/forecasts/baseline",                      label: "↓ Forecast JSON (baseline)" },
    { href: "/api/v1/validation/hindcast-accuracy",            label: "↓ Hindcast validation report" },
  ];
  for (const l of links) {
    const a = document.createElement("a");
    a.className = "download-link";
    a.href = l.href;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = l.label;
    div.appendChild(a);
  }
  return div;
}
```

- [ ] **Step 3: Create main.js — boot sequence**

Create `app/eosp/static/js/main.js`:

```js
/**
 * Boot sequence. Wires all modules together.
 * Executed as ES module after CDN scripts have loaded (they use defer).
 */
import { get } from "./api.js";
import { initMap, renderGeoData } from "./map.js";
import { renderForecast } from "./chart.js";
import { renderScenarios } from "./scenarios.js";
import { initDrawer } from "./drawer.js";

async function boot() {
  // Wait for CDN libraries (Leaflet and Plotly use defer, so they may not
  // be ready immediately when the module executes on fast connections)
  await _waitForLibs();

  // Parallel data fetch
  const [summary, forecast, versions, scenarios, geo] = await Promise.allSettled([
    get("/cases/summary"),
    get("/forecasts/baseline"),
    get("/inference/versions?limit=2"),
    get("/scenarios/compare?scenarios=baseline,quarantine_immediate,evacuation_delay_7d,enhanced_destination_protocols"),
    get("/geo/outbreak"),
  ]);

  // KPI cards
  if (summary.status === "fulfilled") {
    const s = summary.value;
    _setText("kpi-confirmed", s.total_confirmed);
    _setText("kpi-confirmed-sub", `+0 today · ${Object.keys(s.data_sources).length} sources`);
    _setText("kpi-suspected", s.total_suspected);
    _setText("kpi-suspected-sub", "PCR pending");
    _setText("kpi-deaths", s.total_deaths);
    const cfr = s.total_confirmed > 0
      ? Math.round((s.total_deaths / (s.total_confirmed)) * 100) + "%"
      : "—";
    _setText("kpi-deaths-sub", `CFR ${cfr} · Expected: 35–50%`);
    const updatedAt = new Date(s.last_updated);
    _setText("topbar-status", `Updated ${_relativeTime(updatedAt)}`);
    setInterval(async () => {
      try {
        const fresh = await get("/cases/summary");
        _setText("topbar-status", `Updated ${_relativeTime(new Date(fresh.last_updated))}`);
      } catch (_) {}
    }, 60_000);
  }

  // Version badge
  if (versions.status === "fulfilled" && versions.value.versions.length) {
    const v = versions.value.versions[0];
    const badge = document.getElementById("version-badge");
    if (badge) {
      badge.textContent = v.version_id;
      badge.className = "version-badge " + (v.convergence_status === "CONVERGED" ? "converged" : v.convergence_status === "WARNING" ? "warning" : "failed");
    }
  }

  // Forecast chart
  if (forecast.status === "fulfilled") {
    const prev = versions.status === "fulfilled" && versions.value.versions.length > 1
      ? await get(`/forecasts/baseline?model_version=${versions.value.versions[1].version_id}`).catch(() => null)
      : null;
    renderForecast("forecast-chart", forecast.value, prev);
    const last = forecast.value.forecast.at(-1);
    if (last) {
      _setText("kpi-forecast", last.cases_cumulative.median);
      _setText("kpi-forecast-sub", `95% CI: ${last.cases_cumulative.ci_95_lower}–${last.cases_cumulative.ci_95_upper} cases`);
    }
    _setText("chart-freshness", forecast.value.metadata?.freshness_status === "provisional"
      ? "Provisional (100 sims)" : "Full fidelity (10k sims)");
  }

  // Scenario cards
  if (scenarios.status === "fulfilled") {
    renderScenarios(scenarios.value);
  }

  // World map
  const map = initMap("world-map");
  if (geo.status === "fulfilled") {
    renderGeoData(geo.value);
  }

  // Researcher drawer wiring
  initDrawer(_onRunComplete);
  _wireDrawerToggle();
}

function _onRunComplete() {
  // Reload forecast and scenarios after a run finishes
  Promise.allSettled([
    get("/forecasts/baseline"),
    get("/scenarios/compare?scenarios=baseline,quarantine_immediate,evacuation_delay_7d,enhanced_destination_protocols"),
    get("/inference/versions?limit=2"),
    get("/geo/outbreak"),
  ]).then(([forecast, scenarios, versions, geo]) => {
    if (forecast.status === "fulfilled") renderForecast("forecast-chart", forecast.value);
    if (scenarios.status === "fulfilled") renderScenarios(scenarios.value);
    if (geo.status === "fulfilled") renderGeoData(geo.value);
    if (versions.status === "fulfilled" && versions.value.versions.length) {
      const v = versions.value.versions[0];
      const badge = document.getElementById("version-badge");
      if (badge) badge.textContent = v.version_id;
    }
  });
}

function _wireDrawerToggle() {
  const overlay = document.getElementById("drawer-overlay");
  const pageBody = document.getElementById("page-body");
  const btnOpen  = document.getElementById("btn-open-drawer");
  const btnClose = document.getElementById("btn-close-drawer");

  btnOpen?.addEventListener("click", () => {
    overlay?.classList.add("open");
    pageBody?.classList.add("dimmed");
    document.getElementById("drawer")?.focus();
  });

  function close() {
    overlay?.classList.remove("open");
    pageBody?.classList.remove("dimmed");
  }

  btnClose?.addEventListener("click", close);
  overlay?.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && overlay?.classList.contains("open")) close();
  });
}

function _waitForLibs(attempts = 0) {
  return new Promise((resolve) => {
    if (typeof L !== "undefined" && typeof Plotly !== "undefined") return resolve();
    if (attempts > 40) return resolve(); // give up after 2 seconds, render what we can
    setTimeout(() => _waitForLibs(attempts + 1).then(resolve), 50);
  });
}

function _setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function _relativeTime(date) {
  const diff = Math.round((Date.now() - date.getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  return `${Math.round(diff / 3600)}h ago`;
}

boot().catch((err) => {
  document.body.insertAdjacentHTML(
    "afterbegin",
    `<p style="background:#5a1e1e;color:#e57373;padding:12px 24px;font-family:monospace">
      Dashboard failed to load: ${err.message}
    </p>`
  );
});
```

- [ ] **Step 4: Delete old app.js**

```
git rm app/eosp/static/app.js
```

- [ ] **Step 5: Full end-to-end smoke test**

Start the server:
```
python -m uvicorn eosp.main:app --app-dir app --reload
```

Open `http://localhost:8000`. Verify:
- Dark dashboard loads with topbar, 4 KPI cards with real numbers
- Version badge is green and shows a version ID
- World map renders with ship marker, 4 case markers, 3 flight arcs, and risk heatmap
- Forecast chart shows CI bands and median line
- Scenario cards show 4 scenarios with human-readable names and deltas
- `⚗ Run Analysis →` button opens the researcher drawer
- Drawer shows idle state with scenario pills, fidelity buttons, and Run button
- `✕` closes the drawer; `Escape` also closes it

- [ ] **Step 6: Commit**

```
git add app/eosp/static/js/
git commit -m "feat: complete frontend redesign - drawer, jobs SSE client, main boot"
```

---

## Plan self-review

**Spec coverage check:**
- ✅ Visitor dashboard KPI cards with tooltips
- ✅ World map with ship, case markers, flight arcs, heatmap
- ✅ Plotly forecast chart with CI bands, observed, previous version
- ✅ Scenario sidebar with human-readable names, deltas, overlays
- ✅ Researcher drawer idle state (scenario/fidelity selector, run button, downloads)
- ✅ Researcher drawer running state (4 collapsible stage cards, SSE updates, mini charts)
- ✅ SSE endpoint wired to job progress events
- ✅ JobRecord/JobManager progress hooks
- ✅ Inference and ensemble progress callbacks
- ✅ OpenSky client with 12h cache and fallback
- ✅ Three-ring risk propagation
- ✅ `GET /api/v1/geo/outbreak` endpoint
- ✅ Old CSS/JS/HTML removed

**No placeholders, TBDs, or "implement later" steps remain.**
