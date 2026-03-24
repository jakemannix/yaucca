"""Tests for the single-user OAuth 2.1 provider."""

import hashlib
import base64
import sqlite3
import time

import pytest
from pydantic import AnyUrl

from mcp.shared.auth import OAuthClientInformationFull

from yaucca.cloud.oauth_provider import (
    ACCESS_TOKEN_EXPIRY,
    AUTH_CODE_EXPIRY,
    OAuthStore,
    YauccaOAuthProvider,
    _now,
)
from mcp.server.auth.provider import AuthorizationParams


@pytest.fixture()
def store() -> OAuthStore:
    conn = sqlite3.connect(":memory:")
    s = OAuthStore(lambda: conn)
    s.init_schema()
    return s


@pytest.fixture()
def provider(store: OAuthStore) -> YauccaOAuthProvider:
    return YauccaOAuthProvider(store, github_client_id="test-gh-id", github_callback_url="https://localhost/oauth/github/callback")


def _make_client(client_id: str = "test-client") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="test-secret",
        redirect_uris=[AnyUrl("https://claude.ai/oauth/callback")],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope="memory:read memory:write",
    )


def _make_pkce() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256)."""
    verifier = "test-verifier-with-enough-entropy-for-pkce-validation"
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def _make_auth_params(code_challenge: str) -> AuthorizationParams:
    return AuthorizationParams(
        state="test-state",
        scopes=["memory:read", "memory:write"],
        code_challenge=code_challenge,
        redirect_uri=AnyUrl("https://claude.ai/oauth/callback"),
        redirect_uri_provided_explicitly=True,
    )


class TestOAuthStore:
    def test_client_roundtrip(self, store: OAuthStore) -> None:
        client = _make_client()
        store.save_client(client)
        loaded = store.load_client("test-client")
        assert loaded is not None
        assert loaded.client_id == "test-client"
        assert loaded.client_secret == "test-secret"

    def test_client_not_found(self, store: OAuthStore) -> None:
        assert store.load_client("nonexistent") is None

    def test_access_token_roundtrip(self, store: OAuthStore) -> None:
        store.save_token("tok123", "access", "client1", ["read"], expires_at=_now() + 3600)
        loaded = store.load_access_token("tok123")
        assert loaded is not None
        assert loaded.token == "tok123"
        assert loaded.client_id == "client1"
        assert loaded.scopes == ["read"]

    def test_expired_access_token_returns_none(self, store: OAuthStore) -> None:
        store.save_token("expired", "access", "client1", ["read"], expires_at=_now() - 1)
        assert store.load_access_token("expired") is None

    def test_refresh_token_roundtrip(self, store: OAuthStore) -> None:
        store.save_token("ref123", "refresh", "client1", ["read"], expires_at=_now() + 86400)
        loaded = store.load_refresh_token("ref123", "client1")
        assert loaded is not None
        assert loaded.token == "ref123"

    def test_refresh_token_wrong_client(self, store: OAuthStore) -> None:
        store.save_token("ref123", "refresh", "client1", ["read"], expires_at=_now() + 86400)
        assert store.load_refresh_token("ref123", "client2") is None


class TestYauccaOAuthProvider:
    @pytest.mark.anyio()
    async def test_register_and_get_client(self, provider: YauccaOAuthProvider) -> None:
        client = _make_client()
        await provider.register_client(client)
        loaded = await provider.get_client("test-client")
        assert loaded is not None
        assert loaded.client_id == "test-client"

    @pytest.mark.anyio()
    async def test_get_unknown_client_returns_none(self, provider: YauccaOAuthProvider) -> None:
        assert await provider.get_client("unknown") is None

    @pytest.mark.anyio()
    async def test_authorize_redirects_to_github(self, provider: YauccaOAuthProvider) -> None:
        client = _make_client()
        await provider.register_client(client)
        _, challenge = _make_pkce()
        params = _make_auth_params(challenge)

        redirect_url = await provider.authorize(client, params)
        assert "github.com/login/oauth/authorize" in redirect_url
        assert "client_id=test-gh-id" in redirect_url

    @pytest.mark.anyio()
    async def test_complete_authorization_flow(self, provider: YauccaOAuthProvider) -> None:
        """authorize (→ GitHub) → complete_authorization → exchange code → get tokens."""
        client = _make_client()
        await provider.register_client(client)
        _, challenge = _make_pkce()
        params = _make_auth_params(challenge)

        github_url = await provider.authorize(client, params)
        # Extract the pending state from the GitHub redirect URL
        pending_state = github_url.split("state=")[1].split("&")[0]

        # Simulate GitHub callback completing the flow
        redirect_url = provider.complete_authorization(pending_state, "jakemannix")
        assert redirect_url is not None
        assert "code=" in redirect_url
        code = redirect_url.split("code=")[1].split("&")[0]

        # Load and exchange
        auth_code = await provider.load_authorization_code(client, code)
        assert auth_code is not None
        assert auth_code.code_challenge == challenge

        token = await provider.exchange_authorization_code(client, auth_code)
        assert token.access_token
        assert token.refresh_token
        assert token.token_type == "Bearer"
        assert token.expires_in == ACCESS_TOKEN_EXPIRY

        # Verify access token is loadable
        access = await provider.load_access_token(token.access_token)
        assert access is not None
        assert access.client_id == "test-client"

    @pytest.mark.anyio()
    async def test_code_consumed_after_exchange(self, provider: YauccaOAuthProvider) -> None:
        """Auth code should be deleted after exchange."""
        client = _make_client()
        await provider.register_client(client)
        _, challenge = _make_pkce()
        params = _make_auth_params(challenge)

        github_url = await provider.authorize(client, params)
        pending_state = github_url.split("state=")[1].split("&")[0]
        redirect_url = provider.complete_authorization(pending_state, "jakemannix")
        code = redirect_url.split("code=")[1].split("&")[0]

        auth_code = await provider.load_authorization_code(client, code)
        await provider.exchange_authorization_code(client, auth_code)
        assert await provider.load_authorization_code(client, code) is None

    @pytest.mark.anyio()
    async def test_refresh_token_flow(self, provider: YauccaOAuthProvider) -> None:
        """Exchange refresh token for new token pair."""
        client = _make_client()
        await provider.register_client(client)
        _, challenge = _make_pkce()
        params = _make_auth_params(challenge)

        github_url = await provider.authorize(client, params)
        pending_state = github_url.split("state=")[1].split("&")[0]
        redirect_url = provider.complete_authorization(pending_state, "jakemannix")
        code = redirect_url.split("code=")[1].split("&")[0]
        auth_code = await provider.load_authorization_code(client, code)
        token = await provider.exchange_authorization_code(client, auth_code)

        refresh = await provider.load_refresh_token(client, token.refresh_token)
        assert refresh is not None

        new_token = await provider.exchange_refresh_token(client, refresh, ["memory:read"])
        assert new_token.access_token != token.access_token
        assert new_token.refresh_token != token.refresh_token
        assert await provider.load_refresh_token(client, token.refresh_token) is None
        assert await provider.load_access_token(new_token.access_token) is not None

    @pytest.mark.anyio()
    async def test_revoke_token(self, provider: YauccaOAuthProvider) -> None:
        client = _make_client()
        await provider.register_client(client)
        _, challenge = _make_pkce()
        params = _make_auth_params(challenge)

        github_url = await provider.authorize(client, params)
        pending_state = github_url.split("state=")[1].split("&")[0]
        redirect_url = provider.complete_authorization(pending_state, "jakemannix")
        code = redirect_url.split("code=")[1].split("&")[0]
        auth_code = await provider.load_authorization_code(client, code)
        token = await provider.exchange_authorization_code(client, auth_code)

        access = await provider.load_access_token(token.access_token)
        assert access is not None
        await provider.revoke_token(access)
        assert await provider.load_access_token(token.access_token) is None

    @pytest.mark.anyio()
    async def test_pending_auth_expires(self, provider: YauccaOAuthProvider, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client()
        await provider.register_client(client)
        _, challenge = _make_pkce()
        params = _make_auth_params(challenge)

        github_url = await provider.authorize(client, params)
        pending_state = github_url.split("state=")[1].split("&")[0]

        # Fast-forward past expiry
        import yaucca.cloud.oauth_provider as oauth_mod
        monkeypatch.setattr(oauth_mod, "_now", lambda: int(time.time()) + 700)

        assert provider.complete_authorization(pending_state, "jakemannix") is None
