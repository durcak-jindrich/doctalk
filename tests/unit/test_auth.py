"""Entra ID bearer-token validation, in isolation from the network.

`verify_token` is a plain function taking a `Request`, so it is tested
directly rather than through a `TestClient` — no app wiring, no JWKS fetch.
"""

import jwt
import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

from app.api.auth import verify_token
from app.config import settings


def _request(authorization: str | None = None) -> Request:
    headers = Headers({"authorization": authorization} if authorization else {})
    scope = {"type": "http", "headers": headers.raw}
    return Request(scope)


@pytest.fixture(autouse=True)
def _restore_settings():
    """Auth settings are mutated per-test; restore them so tests stay isolated."""
    original = (settings.auth_enabled, settings.azure_tenant_id, settings.azure_client_id)
    yield
    settings.auth_enabled, settings.azure_tenant_id, settings.azure_client_id = original


def test_disabled_allows_a_request_with_no_token():
    settings.auth_enabled = False
    verify_token(_request())  # does not raise


def test_enabled_rejects_a_missing_token():
    settings.auth_enabled = True
    settings.azure_tenant_id, settings.azure_client_id = "tenant", "client"

    with pytest.raises(HTTPException) as exc_info:
        verify_token(_request())
    assert exc_info.value.status_code == 401


def test_enabled_rejects_a_non_bearer_scheme():
    settings.auth_enabled = True
    settings.azure_tenant_id, settings.azure_client_id = "tenant", "client"

    with pytest.raises(HTTPException) as exc_info:
        verify_token(_request("Basic dXNlcjpwYXNz"))
    assert exc_info.value.status_code == 401


def test_enabled_rejects_a_token_that_fails_verification(monkeypatch):
    """A malformed/expired/wrong-audience token — whatever PyJWT rejects — is a 401.

    The JWKS client is stubbed so this stays offline: this test is about
    `verify_token`'s error handling, not the real Entra ID discovery endpoint.
    """
    settings.auth_enabled = True
    settings.azure_tenant_id, settings.azure_client_id = "tenant", "client"

    class ExplodingJWKSClient:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientError("no matching signing key")

    monkeypatch.setattr("app.api.auth._jwks_client", lambda: ExplodingJWKSClient())

    with pytest.raises(HTTPException) as exc_info:
        verify_token(_request("Bearer not-a-real-token"))
    assert exc_info.value.status_code == 401
