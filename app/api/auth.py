"""Entra ID (AAD) bearer-token validation.

Feature-flagged by `settings.auth_enabled` — off locally (nothing to validate
against without a tenant), on in Azure. Applied as a router-level dependency
in `app.main` rather than inside each handler, so no route body needs to know
auth exists.
"""

import logging
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWKClient

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    """Built lazily on first use, not at import time.

    Import time is test collection and every local boot with `auth_enabled`
    false; neither should make a network call. `PyJWKClient` caches keys
    itself, so this only fetches the JWKS document once per process, not once
    per request.
    """
    url = f"https://login.microsoftonline.com/{settings.azure_tenant_id}/discovery/v2.0/keys"
    return PyJWKClient(url, cache_keys=True)


def verify_token(request: Request) -> None:
    """Reject the request unless it carries a valid Entra ID access token.

    A no-op when `auth_enabled` is false — the dependency still runs (it is
    wired unconditionally in `app.main`), it just decides nothing, so there
    is one flag rather than two code paths for whether auth is wired at all.
    """
    if not settings.auth_enabled:
        return

    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token).key
        jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.azure_client_id,
            issuer=f"https://login.microsoftonline.com/{settings.azure_tenant_id}/v2.0",
        )
    except jwt.PyJWTError as exc:
        logger.warning("rejected bearer token: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.") from exc
