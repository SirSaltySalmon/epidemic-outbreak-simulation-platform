# EOSP MVP

Epidemic Outbreak Simulation Platform first executable slice.

This repository implements a runnable FastAPI service and static dashboard shell for the PRD:

- Case summaries, details, validation records, and data quality alerts
- Versioned inference metadata with convergence diagnostics
- Scenario forecasts and scenario comparison responses
- Lightweight dashboard served from `/`

Forecasts are read only from completed simulation results stored in the active repository. If no result exists yet, visitor forecast endpoints return a clear "no simulation results available" error instead of inventing seeded forecast output.

## Run

```powershell
python -m pip install -e ".[test]"
python -m uvicorn eosp.main:app --app-dir app --reload
```

Open `http://127.0.0.1:8000`.

## Local researcher GUI

Run forecasts and case CRUD against `EOSP_DATABASE_URL` from `.env` on this machine (same pipeline as the web Researcher Console, no FastAPI server). After `pip install -e .`:

```powershell
eosp-researcher-gui
```

From a checkout without installing the console script, set `PYTHONPATH` to `app` (see pytest config) and run `python -m eosp.tools.researcher_gui`. This process writes inference outputs and forecasts to the configured database; use a development URL unless you intend to update production.

## Local Persistent Setup

Start local services:

```powershell
Copy-Item .env.example .env
docker compose up -d
python -m alembic upgrade head
python -m eosp.scripts.seed_dev_data
python -m uvicorn eosp.main:app --app-dir app --reload
```

PostgreSQL from Compose is exposed on `127.0.0.1:5433` (container still uses 5432 internally). That keeps host port 5432 free for a separately installed PostgreSQL on Windows. Redis runs on `127.0.0.1:6379`.

## Test

```powershell
python -m pytest
```

### Mobility bundle (open data)

OpenFlights [Airport and Route databases](https://openflights.org/data.html) are under the [**Open Database License (ODbL)**](https://opendatacommons.org/licenses/odbl/). You may commit `data/raw/openflights/airports.dat` and `routes.dat` in this repo (they are not gitignored); attribute the source and respect ODbL share-alike terms if you redistribute derivatives.

The metapop risk map can consume a mobility schedule built from route data. With the project installed (`python -m pip install -e ".[test]"` or similar), run:

```powershell
python -m eosp.scripts.build_mobility_bundle --routes data/raw/openflights/routes.dat --out-json app/eosp/data/bundles/example.json
```

Add `--out-parquet path/to/bundle.parquet` if you want the long-table Parquet form; that requires PyArrow via `python -m pip install -e ".[mobility]"`. The script writes a sidecar `*.meta.json` next to the JSON output with provenance fields you can edit. Generated `app/eosp/data/bundles/*.json` files are **gitignored** (they can be tens of MB).

Point the API at a mobility file with `EOSP_METAPOP_MOBILITY` (absolute or repo-relative path to the `.json` or `.parquet` schedule). The dashboard calls `GET /api/v1/geo/outbreak` without a query flag by default; set **`EOSP_GEO_RISK_MODEL=metapop`** in `.env` so the same UI uses the metapop kernel (otherwise it stays on the legacy OpenSky ring heuristic). Leave `EOSP_GEO_RISK_MODEL=legacy` while iterating.

A **full** OpenFlights-derived graph spans thousands of airports and ~10⁴ edges per simulated day; metapop runs can be **slow** or memory-heavy. For interactive use, prefer the checked-in `mobility_weekly_skeleton.json` (default when `EOSP_METAPOP_MOBILITY` is unset) until you add a **region-filtered** ETL export.
