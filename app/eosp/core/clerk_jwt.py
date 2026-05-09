"""Verify Clerk session JWTs against the instance JWKS (RS256)."""

from __future__ import annotations

from functools import lru_cache

import jwt
from jwt import InvalidTokenError, PyJWKClient


@lru_cache(maxsize=8)
def _jwks_client(frontend_api: str) -> PyJWKClient:
    base = frontend_api.rstrip("/")
    return PyJWKClient(f"{base}/.well-known/jwks.json")


def verify_clerk_session_token(token: str, frontend_api: str) -> dict:
    """Decode and verify a Clerk-issued session token. Raises jwt.InvalidTokenError on failure."""

    issuer = frontend_api.rstrip("/")
    signing_key = _jwks_client(issuer).get_signing_key_from_jwt(token)
    # Session JWTs use RS256; audience varies by Clerk version — signature + issuer are enough here.
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=issuer,
        options={"verify_aud": False},
    )


__all__ = ["verify_clerk_session_token"]
