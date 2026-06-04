"""
Auth middleware — LOCAL DEV version.
JWT validation is bypassed. Every request is treated as authenticated
with a synthetic dev user so you can call the API directly from curl / Postman
without setting up an Entra ID app registration.

DO NOT use this in production. The production version is in the ACA repo.
"""
from __future__ import annotations

import logging

from fastapi import Header

logger = logging.getLogger(__name__)

_DEV_CLAIMS = {
    "oid":         "dev-user-local",
    "unique_name": "dev@local",
    "name":        "Local Dev User",
    "tid":         "local",
    "_raw_token":  "dev-bypass",
}


async def verify_token(authorization: str = Header(default="")) -> dict:
    """
    Local dev: always returns synthetic dev claims.
    Pass any Authorization header (or none) — it's not validated.
    """
    logger.debug("Auth bypass: returning dev claims for local dev")
    return _DEV_CLAIMS


async def verify_token_optional(authorization: str = Header(default="")) -> dict | None:
    return _DEV_CLAIMS
