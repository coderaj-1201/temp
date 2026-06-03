"""
Entra ID (Azure AD) token validation middleware.

Validates Bearer tokens issued by Teams / MSAL to your app registration.
Uses python-jose for JWT decode + fetches JWKS from Microsoft's well-known endpoint.

Flow:
  Teams user opens bot → Teams attaches access token to every request
  → FastAPI Depends(verify_token) validates it
  → Returns claims dict {oid, unique_name, name, tid}
"""
from __future__ import annotations

import logging
from functools import lru_cache

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


@lru_cache(maxsize=1)
def _get_jwks() -> dict:
    """Fetch Microsoft's public signing keys. Cached per process."""
    resp = httpx.get(_JWKS_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


async def verify_token(authorization: str = Header(...)) -> dict:
    """
    FastAPI dependency — validates Entra ID Bearer token.

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
