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

## Local Persistent Setup

Start local services:

```powershell
Copy-Item .env.example .env
docker compose up -d
python -m alembic upgrade head
python -m eosp.scripts.seed_dev_data
python -m uvicorn eosp.main:app --app-dir app --reload
```

PostgreSQL runs on `127.0.0.1:5432`; Redis runs on `127.0.0.1:6379`.

## Test

```powershell
python -m pytest
```

### Mobility bundle (open data)

The metapop risk map can consume a mobility schedule built from open flight route data. Download OpenFlights `routes.dat` into `data/raw/openflights/` (that tree is gitignored for large files). From the repository root, with the project installed (`python -m pip install -e ".[test]"` or similar), run:

```powershell
python -m eosp.scripts.build_mobility_bundle --routes data/raw/openflights/routes.dat --out-json app/eosp/data/bundles/example.json
```

Add `--out-parquet path/to/bundle.parquet` if you want the long-table Parquet form; that requires PyArrow via `python -m pip install -e ".[mobility]"`. The script writes a sidecar `*.meta.json` next to the JSON output with provenance fields you can edit. Point the API at a mobility file with the environment variable `EOSP_METAPOP_MOBILITY` (absolute or repo-relative path to the `.json` or `.parquet` schedule).
