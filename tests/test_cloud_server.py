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
        assert len(resp.json()["passages"]) == 2

    def test_list_passages_by_tag(self, client: TestClient) -> None:
        client.post("/api/passages", json={"text": "Ex", "tags": ["exchange"]})
        client.post("/api/passages", json={"text": "Sum", "tags": ["summary"]})
        resp = client.get("/api/passages", params={"tag": "exchange"})
        assert resp.status_code == 200
        passages = resp.json()["passages"]
        assert len(passages) == 1
        assert passages[0]["text"] == "Ex"

    def test_delete_passage(self, client: TestClient) -> None:
        resp = client.post("/api/passages", json={"text": "To delete"})
        pid = resp.json()["id"]
        resp = client.delete(f"/api/passages/{pid}")
        assert resp.status_code == 200

        # Verify it's gone
        resp = client.get("/api/passages")
        assert len(resp.json()["passages"]) == 0

    def test_delete_passage_not_found(self, client: TestClient) -> None:
        resp = client.delete("/api/passages/nonexistent")
        assert resp.status_code == 404

    def test_search_passages(self, client: TestClient) -> None:
        """Search endpoint returns 200. With async embedding queue,
        newly created passages may not be immediately searchable via vector
        search, but the endpoint should not error."""
        client.post("/api/passages", json={"text": "Fixed authentication bug"})
        client.post("/api/passages", json={"text": "Updated README"})
        resp = client.get("/api/passages/search", params={"q": "authentication"})
        assert resp.status_code == 200

    def test_search_passages_text_fallback(self, client: TestClient) -> None:
        """Text search via list endpoint still works for immediate queries."""
        client.post("/api/passages", json={"text": "Fixed authentication bug"})
        client.post("/api/passages", json={"text": "Updated README"})
        resp = client.get("/api/passages", params={"search": "authentication"})
        assert resp.status_code == 200
        results = resp.json()["passages"]
        assert len(results) == 1
        assert "authentication" in results[0]["text"]

    def test_exclude_tags(self, client: TestClient) -> None:
        """Passages with excluded tags are filtered out."""
        client.post("/api/passages", json={"text": "Active", "tags": ["@next"]})
        client.post("/api/passages", json={"text": "Done", "tags": ["@next", "@done"]})
        resp = client.get("/api/passages", params={"tag": "@next", "exclude_tags": "@done"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["passages"]) == 1
        assert data["passages"][0]["text"] == "Active"
        assert data["excluded_tags"] == ["@done"]

    def test_exclude_tags_empty_override(self, client: TestClient) -> None:
        """Passing empty exclude_tags overrides server default."""
        client.post("/api/passages", json={"text": "Active", "tags": ["@next"]})
        client.post("/api/passages", json={"text": "Done", "tags": ["@next", "@done"]})
        # Empty string = no exclusions (override any server default)
        resp = client.get("/api/passages", params={"tag": "@next", "exclude_tags": ""})
        assert resp.status_code == 200
        assert len(resp.json()["passages"]) == 2


class TestBackfillEndpoint:
    def test_backfill_all(self, client: TestClient) -> None:
        """Backfill endpoint runs without error (stub embedder, no sqlite-vec)."""
        client.post("/api/passages", json={"text": "Passage one"})
        client.post("/api/passages", json={"text": "Passage two"})
        resp = client.post("/api/admin/backfill")
        assert resp.status_code == 200

    def test_backfill_specific_profile(self, client: TestClient) -> None:
        """Backfill a specific profile name."""
        client.post("/api/passages", json={"text": "Test"})
        resp = client.post("/api/admin/backfill", params={"profile": "d1024"})
        assert resp.status_code == 200


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
