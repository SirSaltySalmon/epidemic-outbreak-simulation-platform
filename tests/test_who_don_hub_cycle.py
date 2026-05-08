"""End-to-end WHO DON hub → item ingest cycle (mocked HTTP)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from eosp.core.repository import empty_repository
from eosp.services.who_don_extract import extract_who_don_cohort_payloads
from eosp.services.who_don_hub_cycle import run_who_don_hub_ingest_cycle
from eosp.services.who_don_poller import WhoDonFetchResult

HUB_URL = "https://www.who.int/emergencies/emergency-events/item/2026-e000227"

HUB_HTML = f"""
<html><body>
<p>8 May 2026 | Multi-country</p>
<a href="https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON999">Latest DON</a>
<p>4 May 2026 | Multi-country</p>
<a href="https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON998">Earlier DON</a>
</body></html>
"""

ITEM_HTML = """
<html><body><article>
<p>On 8 May 2026, 6 laboratory-confirmed cases were reported in Spain.</p>
<p>1 deaths have been recorded.</p>
</article></body></html>
"""

ITEM_HTML_B = """
<html><body><article>
<p>On 8 May 2026, 9 laboratory-confirmed cases were reported in Spain.</p>
<p>1 deaths have been recorded.</p>
</article></body></html>
"""


def _result(html: str, fp: str) -> WhoDonFetchResult:
    return WhoDonFetchResult(
        fingerprint=fp,
        status_code=200,
        etag=None,
        last_modified=None,
        html=html,
    )


def test_hub_cycle_latest_only_ingests_new_item() -> None:
    repo = empty_repository()

    def fetch_side_effect(url: str) -> WhoDonFetchResult:
        if url.rstrip("/") == HUB_URL.rstrip("/"):
            return _result(HUB_HTML, "fphub")
        if "2026-DON999" in url:
            return _result(ITEM_HTML, "fpitemv1")
        raise AssertionError(f"unexpected fetch {url!r}")

    with patch("eosp.services.who_don_hub_cycle.fetch_who_don_page", side_effect=fetch_side_effect):
        changed = run_who_don_hub_ingest_cycle(
            repo,
            hub_url=HUB_URL,
            base_feed_key="fktest",
            item_policy="latest_only",
        )
    assert changed is True
    cases = repo.list_cases()
    assert len(cases) == 1
    assert cases[0].cohort_size == 6
    assert cases[0].external_observation_key == "fktest:2026-DON999:aggregate"


def test_hub_cycle_second_poll_idempotent() -> None:
    repo = empty_repository()

    def fetch_side_effect(url: str) -> WhoDonFetchResult:
        if url.rstrip("/") == HUB_URL.rstrip("/"):
            return _result(HUB_HTML, "fphub")
        if "2026-DON999" in url:
            return _result(ITEM_HTML, "fpitemv1")
        raise AssertionError(url)

    with patch("eosp.services.who_don_hub_cycle.fetch_who_don_page", side_effect=fetch_side_effect):
        assert run_who_don_hub_ingest_cycle(repo, hub_url=HUB_URL, base_feed_key="fk2", item_policy="latest_only") is True
        assert run_who_don_hub_ingest_cycle(repo, hub_url=HUB_URL, base_feed_key="fk2", item_policy="latest_only") is False


def test_hub_cycle_item_edit_updates_counts() -> None:
    repo = empty_repository()
    state = {"fp": "fpitemv1", "html": ITEM_HTML}

    def fetch_side_effect(url: str) -> WhoDonFetchResult:
        if url.rstrip("/") == HUB_URL.rstrip("/"):
            return _result(HUB_HTML, "fphub")
        if "2026-DON999" in url:
            return _result(state["html"], state["fp"])
        raise AssertionError(url)

    with patch("eosp.services.who_don_hub_cycle.fetch_who_don_page", side_effect=fetch_side_effect):
        assert run_who_don_hub_ingest_cycle(repo, hub_url=HUB_URL, base_feed_key="fk3", item_policy="latest_only") is True
        assert run_who_don_hub_ingest_cycle(repo, hub_url=HUB_URL, base_feed_key="fk3", item_policy="latest_only") is False
        state["fp"] = "fpitemv2"
        state["html"] = ITEM_HTML_B
        assert run_who_don_hub_ingest_cycle(repo, hub_url=HUB_URL, base_feed_key="fk3", item_policy="latest_only") is True
    assert repo.list_cases()[0].cohort_size == 9


def test_hub_cycle_all_discovered_two_items() -> None:
    repo = empty_repository()

    def fetch_side_effect(url: str) -> WhoDonFetchResult:
        if url.rstrip("/") == HUB_URL.rstrip("/"):
            return _result(HUB_HTML, "fphub")
        if "2026-DON999" in url:
            return _result(ITEM_HTML, "fp999")
        if "2026-DON998" in url:
            return _result(
                """<article><p>3 confirmed cases in Spain.</p></article>""",
                "fp998",
            )
        raise AssertionError(url)

    with patch("eosp.services.who_don_hub_cycle.fetch_who_don_page", side_effect=fetch_side_effect):
        changed = run_who_don_hub_ingest_cycle(
            repo,
            hub_url=HUB_URL,
            base_feed_key="fk4",
            item_policy="all_discovered",
        )
    assert changed is True
    cases = sorted(repo.list_cases(), key=lambda c: c.external_observation_key or "")
    assert len(cases) == 2
    keys = {c.external_observation_key for c in cases}
    assert keys == {"fk4:2026-DON998:aggregate", "fk4:2026-DON999:aggregate"}


def test_extract_don_item_id_external_observation_key() -> None:
    html = "<main>4 confirmed cases in Spain on 10 june 2026</main>"
    rows = extract_who_don_cohort_payloads(html, feed_key="ev", don_item_id="2026-DON123", report_fallback=date(2026, 1, 1))
    assert rows[0].external_observation_key == "ev:2026-DON123:aggregate"
    assert rows[0].patient_identifier == "who_don_ev_2026-DON123_cohort"
