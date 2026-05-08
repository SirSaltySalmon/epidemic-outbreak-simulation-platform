"""Future-facing WHO DON extract checks: SSR-style snapshot + optional live hub→item smoke.

Live check mirrors production: **event hub** → parse ``disease-outbreak-news/item`` links →
**fetch a DON item page** → extract cohorts. The hub HTML alone is not the extract target.

Synthetic HTML rehearses **the fingerprint-change** ingest path once article text is reachable.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from eosp.core.repository import empty_repository
from eosp.core.settings import get_settings
from eosp.services.who_don_discover import list_don_items_from_hub_html, sort_don_entries_newest_first
from eosp.services.who_don_extract import detected_who_don_countries, extract_who_don_cohort_payloads
from eosp.services.who_don_ingest import ingest_who_don_from_html
from eosp.services.who_don_poller import _normalize_who_html, fetch_who_don_page

# Mirrors plausible DON prose listing aggregate counts and jurisdictions.
SYNTH_NEW_DON_SNAPSHOT_HTML = """<!doctype html>
<html lang="en">
<head><title>Hantavirus — DON</title></head>
<body>
<article class="detail">
  <h1>Hantavirus cluster linked to cruise ship travel, multi-country</h1>
  <p>Geneva — On 4 May 2026, WHO was notified of <strong>8 laboratory-confirmed</strong>
     cases among passengers with exposures on international cruises and shore contacts.
     Investigations are ongoing among persons reporting recent travel histories linked to
     South Africa, Spain, Switzerland, the Netherlands, and the United Kingdom.</p>
  <p>Among these cases, <strong>2 deaths</strong> have been reported.</p>
</article>
</body>
</html>
"""


@pytest.fixture
def feed_key() -> str:
    return "who_int_future_snap"


def test_extract_future_snap_eight_cases_multicountry(feed_key: str) -> None:
    territories = detected_who_don_countries(SYNTH_NEW_DON_SNAPSHOT_HTML)
    rows = extract_who_don_cohort_payloads(
        SYNTH_NEW_DON_SNAPSHOT_HTML,
        feed_key=feed_key,
        report_fallback=date(2026, 5, 1),
    )
    assert len(rows) == 1
    r0 = rows[0]
    assert r0.cohort_size == 8
    assert r0.cohort_deaths == 2
    expected = {"ZA", "ES", "CH", "NL", "GB"}
    assert expected.issubset(set(territories))
    assert len(territories) >= 5
    assert r0.location_country == "CV"


def test_ingest_future_snap_as_first_poll(feed_key: str) -> None:
    repo = empty_repository()
    assert ingest_who_don_from_html(repo, SYNTH_NEW_DON_SNAPSHOT_HTML, feed_key=feed_key) is True
    cases = repo.list_cases()
    assert len(cases) == 1
    assert cases[0].cohort_size == 8
    assert ingest_who_don_from_html(repo, SYNTH_NEW_DON_SNAPSHOT_HTML, feed_key=feed_key) is False


_LIVE_ITEM_EXTRACT_MSG = (
    "DON item page had confirmation hints but extractor returned nothing — "
    "widen regex/LLM or inspect focus window."
)


@pytest.mark.integration
def test_who_don_live_hub_crawl_and_item_extract() -> None:
    """Opt-in live probe: hub → DON item links → newest item page → extract.

    Set ``WHO_DON_LIVE=1`` (or ``true`` / ``yes``). Not read by the app runtime.
    """

    if os.environ.get("WHO_DON_LIVE", "").lower() not in ("1", "true", "yes"):
        pytest.skip("Set WHO_DON_LIVE=1 to run live WHO hub->item crawl + extract.")

    settings = get_settings()
    hub_url = settings.who_don_url
    hub = fetch_who_don_page(hub_url)
    entries = list_don_items_from_hub_html(hub.html, hub_url)
    if not entries:
        pytest.skip(
            f"No disease-outbreak-news/item links parsed from hub {hub_url!r}. "
            "WHO may have changed markup; update who_don_discover."
        )

    ordered = sort_don_entries_newest_first(entries)
    entry = ordered[0]
    item = fetch_who_don_page(entry.url)
    lowered = item.html.lower()
    normalized = _normalize_who_html(item.html)
    if "laboratory" not in normalized and "confirmed" not in lowered:
        pytest.skip(
            f"DON item {entry.don_item_id!r} returned no laboratory/confirmed text in normalized HTML. "
            "Page may be client-rendered only."
        )

    rows = extract_who_don_cohort_payloads(
        item.html,
        feed_key=settings.who_don_feed_key,
        don_item_id=entry.don_item_id,
        report_fallback=date(2026, 5, 1),
    )
    assert rows, _LIVE_ITEM_EXTRACT_MSG
