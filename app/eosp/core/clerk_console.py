"""Fetch Clerk user metadata when the session JWT omits it."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_CLERK_BACKEND_USER_AGENT = "EOSP/0.1"


class ClerkBackendApiError(RuntimeError):
    """Raised when Clerk Backend API cannot be queried successfully."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def fetch_clerk_public_metadata(
    user_id: str,
    secret_key: str,
    api_version: str = "2025-04-10",
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Return ``public_metadata`` for a Clerk user via Backend API.

    - On **200**: returns the ``public_metadata`` object (possibly empty).
    - On **404**: returns ``{}`` (unknown user id).
    - On non-404 HTTP errors or transport/JSON failures: raises ``ClerkBackendApiError``.

    Clerk requires exactly one of ``Clerk-API-Version`` header or ``__clerk_api_version`` query
    on direct api.clerk.com requests; omitting both commonly yields **401/403** even with a valid ``sk_`` key.

    See https://clerk.com/docs/reference/backend-api/tag/Users#operation/GetUser
    """

    safe_id = urllib.parse.quote(user_id, safe="")
    url = f"https://api.clerk.com/v1/users/{safe_id}"
    ver = (api_version or "2025-04-10").strip()
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {secret_key.strip()}",
            "Clerk-API-Version": ver,
            "Accept": "application/json",
            "User-Agent": _CLERK_BACKEND_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode()
            data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        body = ""
        try:
            body = exc.read().decode().strip()
        except Exception:
            body = ""
        detail = body[:300] if body else "no response body"
        raise ClerkBackendApiError(
            f"Clerk Backend API HTTP {exc.code}: {detail}",
            status_code=exc.code,
        ) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise ClerkBackendApiError("Clerk Backend API request timed out") from exc
    except Exception as exc:
        raise ClerkBackendApiError("Clerk Backend API request failed") from exc

    pm = data.get("public_metadata")
    return pm if isinstance(pm, dict) else {}
