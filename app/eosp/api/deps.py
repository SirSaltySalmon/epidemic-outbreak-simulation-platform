"""FastAPI dependencies."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any, Mapping

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError

from eosp.core.clerk_console import ClerkBackendApiError, fetch_clerk_public_metadata
from eosp.core.clerk_jwt import verify_clerk_session_token
from eosp.core.settings import get_settings

security = HTTPBearer(auto_error=False)
_CONSOLE_ACCESS_CACHE_TTL_SECONDS = 60.0
_console_access_cache: dict[str, tuple[float, bool]] = {}


async def require_clerk_session(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict[str, Any] | None:
    """When ``EOSP_CLERK_FRONTEND_API`` is unset, returns ``None`` (researcher routes stay open for tests).

    When set, requires a valid Clerk session JWT via ``Authorization: Bearer`` or ``__session`` cookie.
    """

    settings = get_settings()
    frontend_api = (settings.clerk_frontend_api or "").strip()
    if not frontend_api:
        return None

    token: str | None = None
    if credentials is not None:
        token = credentials.credentials
    if not token:
        token = request.cookies.get("__session")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    try:
        claims = verify_clerk_session_token(token, frontend_api)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        ) from exc
    return claims


def claims_console_access(claims: Mapping[str, Any]) -> bool:
    """True when Clerk JWT/session claims grant Console use (mutations, jobs, forecast runs).

    Reads ``public_metadata.console_access`` (Clerk default session token shape) or a top-level
    ``console_access`` claim from a custom JWT template.
    """

    pm = claims.get("public_metadata")
    if isinstance(pm, dict) and pm.get("console_access") is True:
        return True
    return claims.get("console_access") is True


async def require_console_access(
    session: Annotated[dict[str, Any] | None, Depends(require_clerk_session)],
) -> dict[str, Any] | None:
    """Requires ``console_access`` when Clerk is configured.

    Checks JWT claims first. Clerk **default session tokens do not include**
    ``public_metadata``; if claims lack ``console_access`` but ``EOSP_CLERK_SECRET_KEY``
    is set, resolves metadata via Clerk Backend API (see :func:`claims_console_access`
    and ``fetch_clerk_public_metadata``).

    When ``EOSP_CLERK_FRONTEND_API`` is unset, returns ``None`` (same open mode as
    :func:`require_clerk_session` for local tests).
    """

    if session is None:
        return None
    if claims_console_access(session):
        return session

    settings = get_settings()
    secret = (settings.clerk_secret_key or "").strip()
    user_id = session.get("sub")
    if secret and isinstance(user_id, str) and user_id:
        now = time.monotonic()
        cached = _console_access_cache.get(user_id)
        if cached is not None and (now - cached[0]) <= _CONSOLE_ACCESS_CACHE_TTL_SECONDS:
            if cached[1]:
                return session
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Console access required",
            )
        try:
            api_ver = (settings.clerk_backend_api_version or "2025-04-10").strip()
            pm = await asyncio.to_thread(fetch_clerk_public_metadata, user_id, secret, api_ver)
        except ClerkBackendApiError as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code in (401, 403):
                detail = "Clerk backend key rejected while checking console access (401/403)."
            elif status_code == 429:
                detail = "Clerk rate limit reached while checking console access; retry shortly."
            else:
                detail = "Unable to verify console access with Clerk Backend API."
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=detail,
            ) from exc
        allowed = pm.get("console_access") is True
        _console_access_cache[user_id] = (now, allowed)
        if allowed:
            return session

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Console access required",
    )
