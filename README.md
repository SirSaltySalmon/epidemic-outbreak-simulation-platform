# EOSP MVP

Epidemic Outbreak Simulation Platform first executable slice.

This repository implements a runnable FastAPI service and static dashboard shell for the PRD:

- Case summaries, details, validation records, and data quality alerts
- Versioned synthetic inference metadata with convergence diagnostics
- Scenario forecasts and scenario comparison responses
- Lightweight dashboard served from `/`

The current implementation uses deterministic seeded sample data and analytic forecast curves. It is intentionally structured so production services can replace the in-memory repositories with PostgreSQL, Redis, S3, Kafka, and real Bayesian/ABM workers without changing the API surface.

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
