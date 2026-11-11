"""Upstash Redis over HTTPS (`Redis.from_env()` reads ``UPSTASH_REDIS_REST_*``)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from upstash_redis import Redis


def redis_available() -> bool:
    return bool(os.getenv("UPSTASH_REDIS_REST_URL") and os.getenv("UPSTASH_REDIS_REST_TOKEN"))


def get_redis(**client_options: Any) -> Redis | None:
    """Return ``Redis.from_env()`` when REST credentials exist; otherwise ``None``.

    ``client_options`` are forwarded to ``Redis.from_env`` (e.g. ``rest_retries=0``).
    """
    if not redis_available():
        return None
    from upstash_redis import Redis

    return Redis.from_env(**client_options)
