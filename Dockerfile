# Builder: install the package (setuptools needs the `app/` tree — see pyproject [tool.setuptools])
FROM python:3.11 AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN python -m venv .venv

COPY pyproject.toml ./
COPY app ./app/

RUN .venv/bin/pip install --no-cache-dir --upgrade pip \
    && .venv/bin/pip install --no-cache-dir .

# Runtime: slim image + venv + full repo (static files use path `app/eosp/static`)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY . .

EXPOSE 8080

# Fly [http_service] internal_port = 8080
CMD ["/app/.venv/bin/uvicorn", "eosp.main:app", "--host", "0.0.0.0", "--port", "8080"]
