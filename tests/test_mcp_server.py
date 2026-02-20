"""Tests for yaucca.mcp_server module."""

from unittest.mock import AsyncMock

import pytest

import yaucca.mcp_server as mcp_mod
from tests.conftest import make_block_response, make_coding_block_set, make_exchange_passage, make_passage


@pytest.fixture(autouse=True)
def patch_module_state(mock_letta: AsyncMock) -> None:
    """Patch module-level _letta and _agent_id for all tests."""
    mcp_mod._letta = mock_letta
    mcp_mod._agent_id = "agent-test-123"
    mcp_mod._archive_id = None
    yield
    mcp_mod._letta = None
    mcp_mod._agent_id = None
    mcp_mod._archive_id = None


class TestGetMemoryBlock:
    async def test_returns_block_value(self, mock_letta: AsyncMock) -> None:
        mock_letta.agents.blocks.retrieve.return_value = make_block_response(value="Test value")
        result = await mcp_mod.get_memory_block("user")
        assert result == "Test value"
        mock_letta.agents.blocks.retrieve.assert_called_once_with("user", agent_id="agent-test-123")

    async def test_empty_block(self, mock_letta: AsyncMock) -> None:
        mock_letta.agents.blocks.retrieve.return_value = make_block_response(value="")
        result = await mcp_mod.get_memory_block("context")
        assert result == ""


class TestUpdateMemoryBlock:
    async def test_updates_block(self, mock_letta: AsyncMock) -> None:
        result = await mcp_mod.update_memory_block("user", "New value")
        assert "Updated" in result
        mock_letta.agents.blocks.update.assert_called_once_with("user", agent_id="agent-test-123", value="New value")


class TestSearchArchivalMemory:
    async def test_semantic_search(self, mock_letta: AsyncMock) -> None:
        search_result = AsyncMock()
        search_result.results = [AsyncMock(content="Found memory", id="p1")]
        mock_letta.agents.passages.search.return_value = search_result

        result = await mcp_mod.search_archival_memory("test query", count=5)
        assert "Found memory" in result

    async def test_fallback_to_text_search(self, mock_letta: AsyncMock) -> None:
        mock_letta.agents.passages.search.side_effect = Exception("Not supported")
        mock_letta.agents.passages.list.return_value = [
            make_passage(text="Text match"),
        ]

        result = await mcp_mod.search_archival_memory("test query")
        assert "Text match" in result


class TestInsertArchivalMemory:
    async def test_uses_archive_api(self, mock_letta: AsyncMock) -> None:
        # Pre-set archive_id
        mcp_mod._archive_id = "archive-001"
        result = await mcp_mod.insert_archival_memory("New learning")
        assert "archived" in result.lower()
        mock_letta.archives.passages.create.assert_called_once_with("archive-001", text="New learning")

    async def test_resolves_archive_id_from_archives_list(self, mock_letta: AsyncMock) -> None:
        archive = AsyncMock()
        archive.id = "archive-from-list"
        mock_letta.archives.list.return_value = [archive]
        result = await mcp_mod.insert_archival_memory("New learning")
        assert "archived" in result.lower()
        assert mcp_mod._archive_id == "archive-from-list"

    async def test_resolves_archive_id_from_passage_fallback(self, mock_letta: AsyncMock) -> None:
        mock_letta.archives.list.side_effect = Exception("Not supported")
        mock_letta.agents.passages.list.return_value = [
            make_passage(passage_id="p1"),
        ]
        result = await mcp_mod.insert_archival_memory("New learning")
        assert "archived" in result.lower()
        assert mcp_mod._archive_id == "archive-001"

    async def test_error_when_no_archive(self, mock_letta: AsyncMock) -> None:
        mock_letta.archives.list.return_value = []
        mock_letta.agents.passages.list.return_value = []
        result = await mcp_mod.insert_archival_memory("New learning")
        assert "error" in result.lower()
        mock_letta.archives.passages.create.assert_not_called()


class TestListMemoryBlocks:
    async def test_lists_blocks(self, mock_letta: AsyncMock) -> None:
        mock_letta.agents.blocks.list.return_value = make_coding_block_set()
        result = await mcp_mod.list_memory_blocks()
        assert "user" in result
        assert "projects" in result
        assert "patterns" in result


class TestGetRecentMessages:
    async def test_returns_exchange_passages(self, mock_letta: AsyncMock) -> None:
        mock_letta.agents.passages.list.return_value = [
            make_exchange_passage(passage_id="e1", user="Hello", assistant="Hi there"),
            make_passage(passage_id="p2", text="Untagged passage"),  # no exchange tag
        ]

        result = await mcp_mod.get_recent_messages(count=5)
        assert "Hello" in result
        assert "Hi there" in result
        # Should have called passages.list, not messages.list
        mock_letta.agents.passages.list.assert_called_once_with("agent-test-123", limit=10, ascending=False)
