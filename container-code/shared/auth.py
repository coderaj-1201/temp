"""
Entra ID (Azure AD) token validation middleware.

Validates Bearer tokens issued by Teams / MSAL to your app registration.
Uses python-jose for JWT decode + fetches JWKS from Microsoft's well-known endpoint.
JWKS is cached with a 24-hour TTL — Microsoft rotates keys periodically.

Flow:
  Teams user opens bot → Teams attaches access token to every request
  → FastAPI Depends(verify_token) validates it
  → Returns claims dict {oid, unique_name, name, tid, _raw_token}
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from fastapi import Header, HTTPException
from jose import JWTError, jwt

from shared.config import settings

logger = logging.getLogger(__name__)

_JWKS_URL = (
    f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}"
    f"/discovery/v2.0/keys"
)
_ISSUER = (
    f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}/v2.0"
)

_JWKS_TTL_SECONDS = 86_400  # 24 hours

_jwks_cache: Optional[dict] = None
_jwks_fetched_at: float = 0.0


def _get_jwks() -> dict:
    """
    Fetch Microsoft's public signing keys with a 24-hour TTL cache.
    Microsoft rotates keys periodically — lru_cache would hold stale keys forever.
    """
    global _jwks_cache, _jwks_fetched_at
    now = time.monotonic()
    if _jwks_cache is not None and (now - _jwks_fetched_at) < _JWKS_TTL_SECONDS:
        return _jwks_cache
    resp = httpx.get(_JWKS_URL, timeout=10)
    resp.raise_for_status()
    _jwks_cache = resp.json()
    _jwks_fetched_at = now
    logger.info("JWKS refreshed from Microsoft endpoint")
    return _jwks_cache


async def verify_token(authorization: str = Header(...)) -> dict:
    """
    FastAPI dependency — validates Entra ID Bearer token.
    Injects _raw_token into claims so downstream code can forward it.

    Usage:
        @router.post("/api/chat")
        async def chat(req: ChatRequest, claims: dict = Depends(verify_token)):
            user_id = claims["oid"]
    """
    try:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid Authorization header format.")

        token = parts[1]
        jwks = _get_jwks()

        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=settings.AZURE_CLIENT_ID,
            issuer=_ISSUER,
            options={"verify_at_hash": False},
        )
        # Attach raw token so downstream agents can forward it in Authorization headers
        claims["_raw_token"] = token
        return claims

    except JWTError as exc:
        logger.warning("Token validation failed: %s", exc)
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Unexpected auth error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Token verification failed.")


async def verify_token_optional(authorization: str = Header(default="")) -> dict | None:
    """
    Same as verify_token but returns None instead of 401 when no token is provided.
    Useful for endpoints that work both authenticated and unauthenticated (e.g. health).
    """
    if not authorization:
        return None
    return await verify_token(authorization)
