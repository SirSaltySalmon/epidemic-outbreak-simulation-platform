"""Simulation calendar and line-list eligibility for the hub timeline simulator."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from eosp.core.models import CaseRecord, ObservationKind


def _normalize_index_id(index_case_id: object | None) -> str | None:
    if index_case_id is None:
        return None
    if isinstance(index_case_id, UUID):
        return str(index_case_id)
    return str(index_case_id)


def eligible_hub_cases(
    cases: list[CaseRecord],
    *,
    allowed_iatas: frozenset[str],
    index_case_id: object | None = None,
) -> list[CaseRecord]:
    """Cases included in the run: valid hub IATA, both individual and cohort rows.

    The index narrative case (when configured) is excluded from this list entirely
    so it does not affect the anchor, bursts, or cohort spawns.
    """

    skip = _normalize_index_id(index_case_id)
    out: list[CaseRecord] = []
    for c in cases:
        if skip is not None and str(c.case_id) == skip:
            continue
        code = (c.location_airport_code or "").upper()
        if not code or code not in allowed_iatas:
            continue
        out.append(c)
    return out


def eligible_individual_cases(
    cases: list[CaseRecord],
    *,
    allowed_iatas: frozenset[str],
    index_case_id: object | None = None,
) -> list[CaseRecord]:
    """Eligible **individual** observations only (legacy helper + unit tests)."""

    return [
        c
        for c in eligible_hub_cases(cases, allowed_iatas=allowed_iatas, index_case_id=index_case_id)
        if c.observation_kind == ObservationKind.INDIVIDUAL
    ]


def compute_simulation_calendar(
    eligible: list[CaseRecord],
    *,
    horizon_days: int = 30,
) -> tuple[date, list[date]]:
    """Return ``(anchor_date, days)`` where ``anchor`` is min symptom onset and ``days`` has length ``horizon_days``."""

    if not eligible:
        raise ValueError("eligible case list is empty — cannot anchor simulation")
    anchor = min(c.symptom_onset_date for c in eligible)
    days = [anchor + timedelta(days=k) for k in range(horizon_days)]
    return anchor, days
