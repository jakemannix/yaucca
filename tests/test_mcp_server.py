"""Tests for yaucca.mcp_server module — cloud API proxy."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import yaucca.mcp_server as mcp_mod
from tests.conftest import make_block_dict, make_passage_dict


def _mock_response(data: object, status_code: int = 200) -> MagicMock:
    """Create a mock httpx response.

    httpx.Response methods like .json() and .raise_for_status() are synchronous,
    so use MagicMock (not AsyncMock) for the response object.
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


@pytest.fixture(autouse=True)
def patch_module_state() -> None:
    """Patch module-level _client for all tests.

    The client itself is AsyncMock (for await client.get()),
    but its return values are plain MagicMocks (httpx responses are sync).
    """
    mcp_mod._client = AsyncMock()
    yield
    mcp_mod._client = None


class TestGetMemoryBlock:
    async def test_returns_block_value(self) -> None:
        assert mcp_mod._client is not None
        mcp_mod._client.get.return_value = _mock_response(make_block_dict(value="Test value"))
        result = await mcp_mod.get_memory_block("user")
        assert result == "Test value"

    async def test_not_found(self) -> None:
        assert mcp_mod._client is not None
        mcp_mod._client.get.return_value = _mock_response(None, status_code=404)
        result = await mcp_mod.get_memory_block("nonexistent")
        assert "not found" in result.lower()


class TestUpdateMemoryBlock:
    async def test_updates_block(self) -> None:
        assert mcp_mod._client is not None
        mcp_mod._client.put.return_value = _mock_response(make_block_dict(value="New value"))
        result = await mcp_mod.update_memory_block("user", "New value")
        assert "Updated" in result


class TestSearchArchivalMemory:
    async def test_returns_results(self) -> None:
        assert mcp_mod._client is not None
        passages = [make_passage_dict(text="Found memory", passage_id="p1")]
        mcp_mod._client.get.return_value = _mock_response(passages)
        result = await mcp_mod.search_archival_memory("test query", count=5)
        assert "Found memory" in result


class TestInsertArchivalMemory:
    async def test_inserts_passage(self) -> None:
        assert mcp_mod._client is not None
        mcp_mod._client.post.return_value = _mock_response(make_passage_dict(text="New learning"), status_code=201)
        result = await mcp_mod.insert_archival_memory("New learning")
        assert "archived" in result.lower()


class TestListMemoryBlocks:
    async def test_lists_blocks(self) -> None:
        assert mcp_mod._client is not None
        blocks = [
            make_block_dict(label="user", value="some value"),
            make_block_dict(label="projects", value="project data"),
        ]
        mcp_mod._client.get.return_value = _mock_response(blocks)
        result = await mcp_mod.list_memory_blocks()
        assert "user" in result
        assert "projects" in result


class TestGetRecentMessages:
    async def test_returns_exchanges(self) -> None:
        assert mcp_mod._client is not None
        passages = [
            make_passage_dict(text="User: Hello\nAssistant: Hi", tags=["exchange"]),
        ]
        mcp_mod._client.get.return_value = _mock_response(passages)
        result = await mcp_mod.get_recent_messages(count=5)
        assert "Hello" in result
