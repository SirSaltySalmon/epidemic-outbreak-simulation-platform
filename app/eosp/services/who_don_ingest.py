"""Apply parsed WHO DON cohort rows to the repository (idempotent upsert)."""

from __future__ import annotations

import logging
from typing import Any

from eosp.services.who_don_extract import extract_who_don_cohort_payloads

logger = logging.getLogger(__name__)


def ingest_who_don_from_html(
    repository: Any,
    html: str,
    *,
    feed_key: str,
    don_item_id: str | None = None,
) -> bool:
    """Upsert cohort observation(s) from DON **item** HTML.

    Returns ``True`` if any row was inserted or materially updated.
    """

    payloads = extract_who_don_cohort_payloads(html, feed_key=feed_key, don_item_id=don_item_id)
    if not payloads:
        logger.info(
            "WHO DON ingest: no cohort payload extracted for feed_key=%s don_item_id=%s",
            feed_key,
            don_item_id,
        )
        return False
    upsert = getattr(repository, "upsert_external_observation", None)
    if upsert is None:
        logger.warning("Repository has no upsert_external_observation; skipping WHO ingest")
        return False
    changed = False
    for payload in payloads:
        _, _, row_changed = upsert(payload)
        changed = changed or row_changed
    return changed
