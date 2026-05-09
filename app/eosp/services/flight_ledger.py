"""Append-only flight snapshot persistence (design §6.3).

Rows are never updated in place: amendments insert new legs and reference
``supersedes_leg_id``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from eosp.core.tables import FlightLegRow, FlightSnapshotRow


def insert_snapshot_if_absent(session: Session, row: FlightSnapshotRow) -> bool:
    """Return ``True`` if a new row was written, ``False`` if ``snapshot_id`` already exists."""

    existing = session.get(FlightSnapshotRow, row.snapshot_id)
    if existing is not None:
        return False
    session.add(row)
    session.flush()
    return True


def insert_leg(session: Session, row: FlightLegRow) -> FlightLegRow:
    """Insert a leg row; callers must not ``UPDATE`` legs after insert."""

    session.add(row)
    session.flush()
    return row


def amend_leg(
    session: Session,
    *,
    superseded: FlightLegRow,
    replacement: FlightLegRow,
) -> FlightLegRow:
    """Create ``replacement`` with ``supersedes_leg_id`` pointing at ``superseded``."""

    replacement.supersedes_leg_id = superseded.id
    return insert_leg(session, replacement)
