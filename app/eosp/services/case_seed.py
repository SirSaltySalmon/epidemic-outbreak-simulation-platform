"""Case-driven seed bookkeeping and synthetic susceptible pool sizing (Track B)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from eosp.core.case_statistics import total_cohort_persons
from eosp.core.models import CaseRecord


def seed_manifest_from_cases(cases: list[CaseRecord] | None) -> dict[str, Any]:
    """Compact manifest for forecast metadata (reproducibility)."""

    if not cases:
        return {"n_case_rows": 0, "cohort_persons": 0, "anchors": {}}
    cc: Counter[str] = Counter()
    for c in cases:
        code = (c.location_airport_code or "").strip().upper()
        if code:
            cc[code] += int(max(1, c.cohort_size))
    return {
        "n_case_rows": len(cases),
        "cohort_persons": int(total_cohort_persons(cases)),
        "anchors": dict(cc),
    }


def synthetic_susceptible_count(
    *,
    cases: list[CaseRecord] | None,
    base: int,
    contact_cohort_only: bool = False,
) -> int:
    """Extra hub susceptibles (legacy path). Disabled for fixed contact cohort worlds."""

    if contact_cohort_only:
        return 0
    if not cases:
        return max(base, 16)
    n = int(total_cohort_persons(cases))
    return max(base, 16, min(500, 2 * max(1, n)))
