"""WHO DON poller: fingerprinting and change detection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from eosp.core.repository import empty_repository
from eosp.services.who_don_poller import content_fingerprint_from_html, poll_who_don_once


def test_fingerprint_stable_for_same_logical_content() -> None:
    html_a = "<html><main><p>Hello  WHO</p></main></html>"
    html_b = "<html><main><p>Hello\n\nWHO</p></main></html>"
    assert content_fingerprint_from_html(html_a) == content_fingerprint_from_html(html_b)


def test_fingerprint_changes_when_main_text_changes() -> None:
    html_a = "<html><main><p>8 cases</p></main></html>"
    html_b = "<html><main><p>9 cases</p></main></html>"
    assert content_fingerprint_from_html(html_a) != content_fingerprint_from_html(html_b)


def test_poll_first_baseline_no_change_flag() -> None:
    repo = empty_repository()
    fake_body = b"<html><main><h1>Hantavirus</h1><p>update</p></main></html>"
    with patch("eosp.services.who_don_poller.urlopen") as urlopen:
        resp = MagicMock()
        resp.status = 200
        resp.headers = {"ETag": '"abc"', "Last-Modified": "Wed, 01 Jan 2026 00:00:00 GMT"}
        resp.read.return_value = fake_body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        urlopen.return_value = resp
        assert poll_who_don_once(repo, url="https://example.test/event", feed_key="k") == (False, fake_body.decode())


def test_poll_second_fetch_detects_change() -> None:
    repo = empty_repository()
    bodies = [
        b"<html><main><p>v1</p></main></html>",
        b"<html><main><p>v2</p></main></html>",
    ]

    def _open(*_a, **_k):
        resp = MagicMock()
        resp.status = 200
        resp.headers = {}
        resp.read.return_value = bodies.pop(0)
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    with patch("eosp.services.who_don_poller.urlopen", side_effect=_open):
        c1, html1 = poll_who_don_once(repo, url="https://example.test/a", feed_key="k")
        c2, html2 = poll_who_don_once(repo, url="https://example.test/a", feed_key="k")
        assert c1 is False
        assert c2 is True
        assert "v1" in html1 and "v2" in html2
