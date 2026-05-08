"""WHO DON: hub discovery + per-item fetch, fingerprint, and ingest.

.. deprecated::
   Automated polling is **off by default** (`EOSP_WHO_DON_POLL_ENABLED=false`).
   Prefer **manual** case/cohort entry. This module remains for optional experiments.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from eosp.services.who_don_discover import DonListEntry, list_don_items_from_hub_html, sort_don_entries_newest_first
from eosp.services.who_don_ingest import ingest_who_don_from_html
from eosp.services.who_don_poller import fetch_who_don_page

logger = logging.getLogger(__name__)

WhoDonItemPolicy = Literal["latest_only", "all_discovered"]


def _select_items(entries: list[DonListEntry], policy: WhoDonItemPolicy) -> list[DonListEntry]:
    ordered = sort_don_entries_newest_first(entries)
    if policy == "latest_only":
        return ordered[:1]
    return ordered


def run_who_don_hub_ingest_cycle(
    repository: Any,
    *,
    hub_url: str,
    base_feed_key: str,
    item_policy: WhoDonItemPolicy = "latest_only",
) -> bool:
    """Fetch hub, discover DON items, poll each selected item, ingest when content changed.

    Returns ``True`` when any :meth:`upsert_external_observation` reported a data mutation.
    """

    hub_result = fetch_who_don_page(hub_url)
    hub_state_key = f"{base_feed_key}:hub"
    recorder = getattr(repository, "record_external_feed_poll", None)
    if recorder is None:
        logger.warning("Repository has no record_external_feed_poll; skipping WHO hub cycle")
        return False

    recorder(
        hub_state_key,
        hub_result.fingerprint,
        treat_first_poll_as_changed=False,
    )

    entries = list_don_items_from_hub_html(hub_result.html, hub_url)
    if not entries:
        logger.info("WHO DON hub: no disease-outbreak-news/item links found on %s", hub_url)
        return False

    selected = _select_items(entries, item_policy)
    any_ingest_db_change = False

    for entry in selected:
        try:
            item_result = fetch_who_don_page(entry.url)
        except Exception:
            logger.exception("WHO DON item fetch failed for %s", entry.url)
            continue

        item_state_key = f"{base_feed_key}:item:{entry.don_item_id}"
        content_changed = recorder(
            item_state_key,
            item_result.fingerprint,
            treat_first_poll_as_changed=True,
        )
        if not content_changed:
            continue
        ingested = ingest_who_don_from_html(
            repository,
            item_result.html,
            feed_key=base_feed_key,
            don_item_id=entry.don_item_id,
        )
        if ingested:
            any_ingest_db_change = True
        else:
            logger.info(
                "WHO DON item fingerprint changed but ingest produced no DB update (don_item_id=%s)",
                entry.don_item_id,
            )

    return any_ingest_db_change
