"""Upstash Redis over HTTPS (`Redis.from_env()` reads ``UPSTASH_REDIS_REST_*``)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from upstash_redis import Redis


def redis_available() -> bool:
    return bool(os.getenv("UPSTASH_REDIS_REST_URL") and os.getenv("UPSTASH_REDIS_REST_TOKEN"))


def get_redis() -> Redis | None:
    """Return ``Redis.from_env()`` when REST credentials exist; otherwise ``None``."""
    if not redis_available():
        return None
    from upstash_redis import Redis

    return Redis.from_env()
