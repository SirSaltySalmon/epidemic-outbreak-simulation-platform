"""Shared response cache for the public read endpoints (Upstash Redis over HTTPS).

The dashboard is read-heavy and its data only changes on a case write, an
inference refit, or a forecast run. Serialized responses are therefore kept in
Redis and served with one round trip instead of a dozen Postgres queries.

Layout (``ns`` is derived from the environment + database URL so two databases
sharing one Redis never see each other's entries):

* ``{ns}:gen`` — integer generation, bumped by every repository write.
* ``{ns}:entries`` — hash of ``cache key -> "{gen}|{stored_at}|{json body}"``.

An entry is only served when its generation matches the current one and it is
younger than the TTL. A reader that started before a write stores its (stale)
body under the old generation, so it is never served — no read/write race.
The TTL only bounds staleness from writers that bypass this process's Redis
(e.g. a local script run without the ``UPSTASH_REDIS_REST_*`` variables).

Every Redis failure degrades to "no cache": reads fall through to Postgres.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from eosp.core.redis_client import get_redis
from eosp.core.settings import get_settings

logger = logging.getLogger("eosp.read_cache")

#: Upstash rejects oversized requests (1 MB on the free tier); skip rather than fail.
_MAX_BODY_BYTES = 900_000
#: After a Redis error, skip the cache for this long instead of paying a timeout per request.
_FAILURE_COOLDOWN_SECONDS = 30.0
#: ``upstash_redis`` builds its httpx client with ``timeout=None``; a hung Redis must not hang reads.
_REQUEST_TIMEOUT_SECONDS = 2.0


class ReadCache:
    def __init__(self, redis: Any, *, namespace: str, ttl_seconds: int) -> None:
        self._redis = redis
        self._gen_key = f"{namespace}:gen"
        self._entries_key = f"{namespace}:entries"
        self._ttl = int(ttl_seconds)
        self._down_until = 0.0

    def lookup(self, key: str) -> tuple[str | None, str | None]:
        """Return ``(body, token)``.

        ``body`` is the cached JSON text or ``None`` on a miss. ``token`` is the
        generation observed by this read and must be passed to :meth:`store`;
        it is ``None`` when the cache is unavailable (then do not store).
        """

        if time.monotonic() < self._down_until:
            return None, None
        try:
            pipe = self._redis.pipeline()
            pipe.get(self._gen_key)
            pipe.hget(self._entries_key, key)
            gen_raw, entry = pipe.exec()
        except Exception as exc:  # noqa: BLE001 - cache must never break a read
            self._trip("lookup", exc, _FAILURE_COOLDOWN_SECONDS)
            return None, None

        token = str(gen_raw) if gen_raw is not None else "0"
        if not isinstance(entry, str):
            return None, token
        entry_gen, _, rest = entry.partition("|")
        stored_at_raw, sep, body = rest.partition("|")
        if not sep or entry_gen != token:
            return None, token
        try:
            age = time.time() - float(stored_at_raw)
        except ValueError:
            return None, token
        if age > self._ttl:
            return None, token
        return body, token

    def store(self, key: str, body: str, token: str | None) -> None:
        if token is None or time.monotonic() < self._down_until:
            return
        if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
            logger.debug("read cache: %s body too large to cache; skipping", key)
            return
        try:
            pipe = self._redis.pipeline()
            pipe.hset(self._entries_key, key, f"{token}|{time.time():.3f}|{body}")
            # Garbage collection only; freshness is enforced per entry in lookup().
            pipe.expire(self._entries_key, self._ttl)
            pipe.exec()
        except Exception as exc:  # noqa: BLE001
            self._trip("store", exc, _FAILURE_COOLDOWN_SECONDS)

    def invalidate(self) -> None:
        """Bump the generation so every stored entry stops being served."""

        try:
            self._redis.incr(self._gen_key)
        except Exception as exc:  # noqa: BLE001
            # Entries from before this write may still be in Redis; this process
            # stops reading them until they have aged out.
            self._trip("invalidate", exc, float(self._ttl))

    def _trip(self, op: str, exc: Exception, cooldown: float) -> None:
        self._down_until = max(self._down_until, time.monotonic() + cooldown)
        logger.warning("read cache: %s failed (%s); bypassing cache for %.0fs", op, exc, cooldown)


def _apply_request_timeout(redis: Any) -> bool:
    """Give the Upstash client's httpx session a finite timeout. Returns False if it cannot."""

    client = getattr(getattr(redis, "_http", None), "_client", None)
    if client is None or not hasattr(client, "timeout"):
        return False
    client.timeout = _REQUEST_TIMEOUT_SECONDS
    return True


def build_read_cache(database_url: str | None) -> ReadCache | None:
    """Create the cache when Upstash credentials exist and it is not disabled; else ``None``."""

    settings = get_settings()
    if not settings.read_cache_enabled or settings.read_cache_ttl_seconds <= 0:
        return None
    try:
        # No client-side retries: the default retry sleeps 3 s, far worse than a cache miss.
        redis = get_redis(rest_retries=0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("read cache: could not create Redis client (%s); cache disabled", exc)
        return None
    if redis is None:
        return None
    if not _apply_request_timeout(redis):
        logger.warning(
            "read cache: cannot set a request timeout on this upstash-redis version; cache disabled"
        )
        return None
    seed = f"{settings.env}|{database_url or ''}"
    namespace = "eosp:read:v1:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return ReadCache(redis, namespace=namespace, ttl_seconds=settings.read_cache_ttl_seconds)
