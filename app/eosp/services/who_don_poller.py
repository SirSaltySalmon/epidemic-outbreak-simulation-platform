"""Poll the WHO Disease Outbreak News event page for updates.

.. deprecated::
   The production **background** poll loop is **deprecated** (default
   ``EOSP_WHO_DON_POLL_ENABLED=false``). Prefer manual data entry. Helpers here remain
   for optional use and tests.

Compares a stable content fingerprint (normalized text + HTTP validators). When the
fingerprint changes, callers should run :func:`ingest_who_don_from_html` with the
fetched HTML so cohort observations stay in sync.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_S = 60
_USER_AGENT = (
    "EOSP/0.2 WHO-DON-poller (epidemic simulation data check; contact: local operator)"
)


@dataclass
class WhoDonFetchResult:
    fingerprint: str
    status_code: int
    etag: str | None
    last_modified: str | None
    html: str


def content_fingerprint_from_html(
    html: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> str:
    """Build a fingerprint resilient to reordering of HTTP validators and markup noise."""
    normalized = _normalize_who_html(html)
    raw = f"{etag or ''}\n{last_modified or ''}\n{normalized}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def fetch_who_don_page(url: str) -> WhoDonFetchResult:
    req = Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        method="GET",
    )
    with urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
        status = getattr(resp, "status", 200)
        etag = _header_get(resp.headers, "etag") if resp.headers else None
        last_modified = _header_get(resp.headers, "last-modified") if resp.headers else None
        body = resp.read()
    html = body.decode("utf-8", errors="replace")
    fp = content_fingerprint_from_html(html, etag=etag, last_modified=last_modified)
    return WhoDonFetchResult(
        fingerprint=fp,
        status_code=int(status),
        etag=etag,
        last_modified=last_modified,
        html=html,
    )


def poll_who_don_once(repository: Any, *, url: str, feed_key: str) -> tuple[bool, str]:
    """Fetch the DON page, persist fingerprint, return ``(changed, html)``.

    ``changed`` is ``True`` when the fingerprint differs from the last stored value
    (excluding the first-ever successful poll, which only establishes a baseline).
    """

    try:
        result = fetch_who_don_page(url)
    except HTTPError as exc:
        logger.warning("WHO DON fetch HTTP error %s for %s", exc.code, url)
        raise
    except URLError as exc:
        logger.warning("WHO DON fetch failed for %s: %s", url, exc.reason)
        raise
    except TimeoutError:
        logger.warning("WHO DON fetch timed out for %s", url)
        raise

    logger.info(
        "WHO DON poll ok status=%s key=%s fingerprint=%s…",
        result.status_code,
        feed_key,
        result.fingerprint[:16],
    )
    recorder = getattr(repository, "record_external_feed_poll", None)
    if recorder is None:
        logger.warning("Repository has no record_external_feed_poll; skipping persistence")
        return False, result.html
    changed = bool(recorder(feed_key, result.fingerprint))
    return changed, result.html


def _header_get(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        raw = headers.get(name)
    except AttributeError:
        raw = headers[name] if name in headers else None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("latin-1", errors="replace")
    return str(raw)


def _normalize_who_html(html: str) -> str:
    # Prefer <main> / <article> when present to drop chrome/navigation.
    fragment = _extract_main_or_article(html)
    text = fragment
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _extract_main_or_article(html: str) -> str:
    for tag in ("main", "article"):
        m = re.search(rf"<{tag}[^>]*>(?P<body>.*?)</{tag}>", html, flags=re.I | re.DOTALL)
        if m:
            return m.group("body")
    return html
