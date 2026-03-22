"""Tests for yaucca.cloud.server — FastAPI HTTP API."""

import pytest
from fastapi.testclient import TestClient

from yaucca.cloud.server import create_app


@pytest.fixture
def client():  # type: ignore[no-untyped-def]
    """Create a test client with in-memory database."""
    app = create_app(db_path=":memory:")
    with TestClient(app) as c:
        yield c


class TestHealth:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestBlockEndpoints:
    def test_list_blocks(self, client: TestClient) -> None:
        resp = client.get("/api/blocks")
        assert resp.status_code == 200
        labels = {b["label"] for b in resp.json()}
        assert labels == {"user", "projects", "patterns", "learnings", "context"}

    def test_get_block(self, client: TestClient) -> None:
        resp = client.get("/api/blocks/user")
        assert resp.status_code == 200
        assert resp.json()["label"] == "user"

    def test_get_block_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/blocks/nonexistent")
        assert resp.status_code == 404

    def test_update_block(self, client: TestClient) -> None:
        resp = client.put("/api/blocks/user", json={"value": "Updated content"})
        assert resp.status_code == 200
        assert resp.json()["value"] == "Updated content"

        # Verify it persisted
        resp = client.get("/api/blocks/user")
        assert resp.json()["value"] == "Updated content"

    def test_update_block_not_found(self, client: TestClient) -> None:
        resp = client.put("/api/blocks/nonexistent", json={"value": "test"})
        assert resp.status_code == 404

    def test_update_block_exceeds_limit(self, client: TestClient) -> None:
        resp = client.put("/api/blocks/user", json={"value": "x" * 6000})
        assert resp.status_code == 400
        assert "char limit" in resp.json()["detail"]


class TestPassageEndpoints:
    def test_create_passage(self, client: TestClient) -> None:
        resp = client.post(
            "/api/passages",
            json={"text": "Test passage", "tags": ["exchange"]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["text"] == "Test passage"
        assert data["tags"] == ["exchange"]
        assert "id" in data

    def test_list_passages(self, client: TestClient) -> None:
        client.post("/api/passages", json={"text": "First"})
        client.post("/api/passages", json={"text": "Second"})
        resp = client.get("/api/passages")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_passages_by_tag(self, client: TestClient) -> None:
        client.post("/api/passages", json={"text": "Ex", "tags": ["exchange"]})
        client.post("/api/passages", json={"text": "Sum", "tags": ["summary"]})
        resp = client.get("/api/passages", params={"tag": "exchange"})
        assert resp.status_code == 200
        passages = resp.json()
        assert len(passages) == 1
        assert passages[0]["text"] == "Ex"

    def test_delete_passage(self, client: TestClient) -> None:
        resp = client.post("/api/passages", json={"text": "To delete"})
        pid = resp.json()["id"]
        resp = client.delete(f"/api/passages/{pid}")
        assert resp.status_code == 200

        # Verify it's gone
        resp = client.get("/api/passages")
        assert len(resp.json()) == 0

    def test_delete_passage_not_found(self, client: TestClient) -> None:
        resp = client.delete("/api/passages/nonexistent")
        assert resp.status_code == 404

    def test_search_passages_fallback(self, client: TestClient) -> None:
        """Without sqlite-vec, search falls back to text search."""
        client.post("/api/passages", json={"text": "Fixed authentication bug"})
        client.post("/api/passages", json={"text": "Updated README"})
        resp = client.get("/api/passages/search", params={"q": "authentication"})
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert "authentication" in results[0]["text"]


class TestAuth:
    def test_auth_enforced_when_token_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("YAUCCA_AUTH_TOKEN", "secret123")
        app = create_app(db_path=":memory:")
        with TestClient(app) as client:
            # No auth → 401
            resp = client.get("/api/blocks")
            assert resp.status_code == 401

            # Wrong token → 401
            resp = client.get("/api/blocks", headers={"Authorization": "Bearer wrong"})
            assert resp.status_code == 401

            # Correct token → 200
            resp = client.get("/api/blocks", headers={"Authorization": "Bearer secret123"})
            assert resp.status_code == 200

            # Health is always public
            resp = client.get("/health")
            assert resp.status_code == 200
