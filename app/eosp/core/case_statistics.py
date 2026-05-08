"""Person-equivalent aggregates over :class:`CaseRecord` lists (cohort + individual)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from eosp.core.models import CaseRecord, CaseStatus, ObservationKind


def total_cohort_persons(cases: Iterable[CaseRecord]) -> int:
    return sum(c.cohort_size for c in cases)


def total_death_equivalents(cases: Iterable[CaseRecord]) -> int:
    n = 0
    for c in cases:
        if c.observation_kind == ObservationKind.COHORT:
            n += c.cohort_deaths
        elif c.death_date is not None:
            n += c.cohort_size
    return n


def validation_quality_weighted_mean(cases: Iterable[CaseRecord]) -> float:
    rows = list(cases)
    if not rows:
        return 0.84
    denom = sum(c.cohort_size for c in rows)
    if denom <= 0:
        return 0.84
    return round(sum(c.validation_score * c.cohort_size for c in rows) / denom, 4)


def case_summary_person_totals(
    cases: Iterable[CaseRecord],
) -> tuple[int, int, int, dict[str, int]]:
    """Return (confirmed_persons, suspected_persons, death_persons, data_sources_person_counts)."""

    confirmed = 0
    suspected = 0
    deaths = 0
    sources: dict[str, int] = defaultdict(int)
    for c in cases:
        w = c.cohort_size
        sources[c.data_source] += w
        if c.confirmed_or_suspected == CaseStatus.CONFIRMED:
            confirmed += w
        else:
            suspected += w
        if c.observation_kind == ObservationKind.COHORT:
            deaths += c.cohort_deaths
        elif c.death_date is not None:
            deaths += c.cohort_size
    return confirmed, suspected, deaths, dict(sources)
