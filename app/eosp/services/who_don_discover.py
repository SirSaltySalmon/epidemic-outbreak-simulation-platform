"""Discover Disease Outbreak News item links from a WHO emergency-event hub page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlparse

DON_ITEM_PATH_MARKER = "/emergencies/disease-outbreak-news/item/"

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass(frozen=True, slots=True)
class DonListEntry:
    """One DON item row discovered on the hub page."""

    url: str
    don_item_id: str
    list_date: date | None


def _don_item_id_from_url(absolute_url: str) -> str | None:
    path = urlparse(absolute_url).path
    if DON_ITEM_PATH_MARKER.lower() not in path.lower():
        return None
    idx = path.lower().index(DON_ITEM_PATH_MARKER.lower()) + len(DON_ITEM_PATH_MARKER)
    slug = path[idx:].strip("/").split("/")[0]
    return slug or None


def _parse_date_before_anchor(html: str, anchor_start: int) -> date | None:
    window = html[max(0, anchor_start - 400) : anchor_start]
    matches = list(re.finditer(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b", window))
    if not matches:
        return None
    # Prefer the last date in the window (closest to the anchor).
    m = matches[-1]
    d, mon_s, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    mon = _MONTHS.get(mon_s)
    if mon is None:
        return None
    try:
        return date(y, mon, d)
    except ValueError:
        return None


def list_don_items_from_hub_html(html: str, hub_url: str) -> list[DonListEntry]:
    """Return DON list entries in **document order** (deduped by ``don_item_id``)."""

    out: list[DonListEntry] = []
    seen: set[str] = set()

    for m in re.finditer(
        r'<a\s+[^>]*href\s*=\s*["\']([^"\']+)["\']',
        html,
        flags=re.I,
    ):
        raw_href = m.group(1).strip()
        if not raw_href or raw_href.startswith(("#", "javascript:")):
            continue
        absolute = urljoin(hub_url, raw_href)
        don_id = _don_item_id_from_url(absolute)
        if don_id is None or don_id in seen:
            continue
        seen.add(don_id)
        list_date = _parse_date_before_anchor(html, m.start())
        out.append(DonListEntry(url=absolute, don_item_id=don_id, list_date=list_date))

    return out


def sort_don_entries_newest_first(entries: list[DonListEntry]) -> list[DonListEntry]:
    """Sort by parsed list date descending; missing dates sort after dated rows."""

    return sorted(
        entries,
        key=lambda e: (e.list_date or date(1970, 1, 1), e.don_item_id),
        reverse=True,
    )
