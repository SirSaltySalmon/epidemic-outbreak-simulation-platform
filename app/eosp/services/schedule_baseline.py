"""Static flight schedule baseline (Track B): JSON bundle + SHA-256 snapshot id."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from eosp.core.tables import FlightLegRow, FlightSnapshotRow
from eosp.services.flight_ledger import insert_leg, insert_snapshot_if_absent


_DEFAULT_BASELINE_PATH = Path(__file__).resolve().parent.parent / "data" / "flight_schedules_baseline.json"


def load_baseline_schedule(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else _DEFAULT_BASELINE_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def canonical_baseline_for_hash(data: dict[str, Any]) -> dict[str, Any]:
    """Subset used for ``snapshot_id`` (stable across cosmetic fields)."""

    flights = data.get("flights") or []
    canon_flights: list[dict[str, Any]] = []
    for f in sorted(flights, key=lambda x: (int(x.get("depart_day", 0)), str(x.get("key", "")))):
        canon_flights.append({
            "depart_day": int(f["depart_day"]),
            "destination_code": str(f["destination_code"]),
            "edge_weight": float(f.get("edge_weight", 0.55)),
            "key": str(f["key"]),
            "n_passengers": int(f["n_passengers"]),
            "origin_iata": str(f.get("origin_iata", "")),
        })
    out: dict[str, Any] = {
        "flights": canon_flights,
        "horizon_end_date": str(data.get("horizon_end_date", "")),
        "horizon_start_date": str(data.get("horizon_start_date", "")),
        "provider_id": str(data.get("provider_id", "baseline_static")),
        "schema_version": int(data.get("schema_version", 1)),
    }
    sha = data.get("openflights_routes_sha256")
    if sha:
        out["openflights_routes_sha256"] = str(sha)
    return out


def compute_snapshot_id(data: dict[str, Any]) -> str:
    canonical = canonical_baseline_for_hash(data)
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ingest_baseline_into_session(session: Session, data: dict[str, Any] | None = None) -> str:
    """Persist snapshot + legs for tests/ops; returns ``snapshot_id``."""

    payload = deepcopy(data) if data is not None else load_baseline_schedule()
    snapshot_id = compute_snapshot_id(payload)
    snap = FlightSnapshotRow(
        snapshot_id=snapshot_id,
        provider_id=str(payload.get("provider_id", "baseline_static")),
        query_fingerprint=f"baseline_json:{payload.get('bundle_date', '')}",
        fetched_at_utc=datetime.now(UTC),
        horizon_start_date=date.fromisoformat(str(payload["horizon_start_date"])),
        horizon_end_date=date.fromisoformat(str(payload["horizon_end_date"])),
        status="complete",
    )
    insert_snapshot_if_absent(session, snap)
    existing = session.scalar(select(FlightLegRow.id).where(FlightLegRow.snapshot_id == snapshot_id).limit(1))
    if existing is None:
        day0 = snap.horizon_start_date
        for f in payload.get("flights") or []:
            dep_day = int(f["depart_day"])
            dep_dt = datetime(day0.year, day0.month, day0.day, 12, 0, 0, tzinfo=UTC) + timedelta(days=dep_day - 1)
            arr_dt = dep_dt + timedelta(hours=11)
            dest_iata = str(f["destination_code"]).split("_")[-1][:8]
            origin_iata = str(f.get("origin_iata") or "").strip().upper() or dest_iata
            insert_leg(
                session,
                FlightLegRow(
                    snapshot_id=snapshot_id,
                    origin_iata=origin_iata[:8],
                    destination_iata=dest_iata,
                    scheduled_departure_utc=dep_dt,
                    scheduled_arrival_utc=arr_dt,
                    provider_record_id=str(f.get("key", "")),
                    leg_status="active",
                ),
            )
    return snapshot_id
