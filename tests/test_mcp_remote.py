"""Integration tests for the composite app (REST API + remote MCP)."""

import pytest
from fastapi.testclient import TestClient

from yaucca.cloud.server import create_composite_app


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    """Create a test client with composite app (REST + MCP)."""
    app = create_composite_app(
        db_path=":memory:",
        issuer_url="https://testserver",
    )
    with TestClient(app) as c:
        yield c


class TestRESTAPIStillWorks:
    """Verify existing REST API routes work alongside MCP."""

    def test_health(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_list_blocks(self, client: TestClient) -> None:
        resp = client.get("/api/blocks")
        assert resp.status_code == 200
        labels = {b["label"] for b in resp.json()}
        assert "user" in labels

    def test_create_passage(self, client: TestClient) -> None:
        resp = client.post("/api/passages", json={"text": "test passage"})
        assert resp.status_code == 201


class TestOAuthMetadataEndpoints:
    """Verify OAuth discovery endpoints are reachable."""

    def test_well_known_oauth_server(self, client: TestClient) -> None:
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.json()
        assert "testserver" in data["issuer"]
        assert "/authorize" in data["authorization_endpoint"]
        assert "/token" in data["token_endpoint"]
        assert "S256" in data["code_challenge_methods_supported"]

    def test_well_known_protected_resource(self, client: TestClient) -> None:
        resp = client.get("/.well-known/oauth-protected-resource/mcp")
        assert resp.status_code == 200
        data = resp.json()
        assert "testserver" in str(data["authorization_servers"])

    def test_register_endpoint(self, client: TestClient) -> None:
        """Dynamic client registration should accept valid metadata."""
        resp = client.post(
            "/register",
            json={
                "redirect_uris": ["https://example.com/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "memory:read memory:write",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "client_id" in data
        assert "client_secret" in data


def _get_oauth_token_via_store(client: TestClient) -> str:
    """Get an access token by writing directly to the OAuth store.

    The full OAuth→GitHub→callback flow is tested in test_oauth_provider.py.
    Here we just need a valid token to test MCP tool calls.
    """
    import secrets as sec

    from yaucca.cloud.oauth_provider import ACCESS_TOKEN_EXPIRY, _now
    from yaucca.cloud.server import _get_db

    db = _get_db()
    token = sec.token_urlsafe(32)
    db.conn.execute(
        "INSERT INTO oauth_tokens (token, token_type, client_id, scopes, expires_at, resource) "
        "VALUES (?, 'access', 'test-client', 'memory:read memory:write', ?, NULL)",
        (token, _now() + ACCESS_TOKEN_EXPIRY),
    )
    db.conn.commit()
    return token


class TestMCPEndpoint:
    """Verify the /mcp endpoint exists and requires auth."""

    def test_authorize_redirects_to_github(self, client: TestClient) -> None:
        """Authorize should redirect to GitHub, not auto-approve."""
        import base64
        import hashlib
        import secrets as sec
        reg = client.post("/register", json={
            "redirect_uris": ["https://example.com/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "scope": "memory:read memory:write",
        })
        cid = reg.json()["client_id"]
        verifier = sec.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        auth = client.get("/authorize", params={
            "client_id": cid, "redirect_uri": "https://example.com/callback",
            "response_type": "code", "scope": "memory:read memory:write",
            "state": "s", "code_challenge": challenge, "code_challenge_method": "S256",
        }, follow_redirects=False)
        assert auth.status_code == 302
        assert "github.com/login/oauth/authorize" in auth.headers["location"]

    def test_mcp_unauthenticated_returns_401(self, client: TestClient) -> None:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert resp.status_code == 401

    def test_mcp_full_tool_call(self, client: TestClient) -> None:
        """OAuth → MCP initialize → list tools → call tool."""
        token = _get_oauth_token_via_store(client)
        hdrs = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        # Initialize
        resp = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        }, headers=hdrs)
        assert resp.status_code == 200
        # Stateless mode: session_id may or may not be present
        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            hdrs["mcp-session-id"] = session_id

        # Initialized notification
        client.post("/mcp", json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }, headers=hdrs)

        # List tools
        tools_resp = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        }, headers=hdrs)
        assert tools_resp.status_code == 200
        tool_names = [t["name"] for t in tools_resp.json()["result"]["tools"]]
        assert "list_memory_blocks" in tool_names
        assert "search_archival_memory" in tool_names
        assert "get_passages" in tool_names
        assert "list_passages_by_tag" in tool_names
        assert "update_passage_tags" in tool_names
        assert len(tool_names) == 9

        # Call list_memory_blocks
        call_resp = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "list_memory_blocks", "arguments": {},
            },
        }, headers=hdrs)
        assert call_resp.status_code == 200
        content = call_resp.json()["result"]["content"]
        assert len(content) > 0
        assert "user" in content[0]["text"]
