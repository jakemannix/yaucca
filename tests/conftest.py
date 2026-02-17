"""Shared test fixtures and factory functions for yaucca tests.

Provides realistic mock objects based on letta-client 1.7.6 types
and helpers for testing MCP tools and hooks.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from letta_client.types import BlockResponse, Passage

# --- Factory functions ---


def make_block_response(
    block_id: str = "block-123",
    label: str = "user",
    value: str = "The user prefers Python and async patterns.",
    limit: int = 5000,
    description: str = "Core memory block",
) -> BlockResponse:
    """Create a realistic BlockResponse matching letta-client 1.7.6."""
    return BlockResponse(
        id=block_id,
        value=value,
        label=label,
        description=description,
        is_template=False,
        read_only=False,
        limit=limit,
        metadata=None,
        tags=None,
        created_by_id=None,
        last_updated_by_id=None,
        project_id="project-456",
        base_template_id=None,
        template_id=None,
        template_name=None,
        deployment_id=None,
        entity_id=None,
        hidden=False,
        preserve_on_migration=False,
    )


def make_coding_block_set() -> list[BlockResponse]:
    """Create the 5-block set for a coding-focused agent.

    Returns blocks in arbitrary order — prompt.py's BLOCK_ORDER handles sorting.
    """
    return [
        make_block_response(
            block_id="b-user",
            label="user",
            value="Jake Mannix, Technical Fellow at Walmart. Prefers Python, async, uv.",
            limit=5000,
            description="Information about the user — preferences, projects, work style",
        ),
        make_block_response(
            block_id="b-projects",
            label="projects",
            value="Nameless agent: stateful AI agent with Claude SDK + Letta. yaucca: memory for Claude Code.",
            limit=10000,
            description="Active projects, repos, and goals being worked on",
        ),
        make_block_response(
            block_id="b-patterns",
            label="patterns",
            value="Uses ruff for linting, pytest for tests, hatchling for builds.",
            limit=10000,
            description="Recurring patterns, conventions, preferred tools and approaches",
        ),
        make_block_response(
            block_id="b-learnings",
            label="learnings",
            value="archives.passages.create bypasses Letta LLM loop — always prefer it.",
            limit=10000,
            description="Hard-won insights, debugging lessons, things that worked or didn't",
        ),
        make_block_response(
            block_id="b-context",
            label="context",
            value="Working on yaucca implementation. Phase 1 complete.",
            limit=5000,
            description="Current session context — what we're working on, recent decisions",
        ),
    ]


def make_passage(
    passage_id: str = "passage-789",
    text: str = "A memory about a coding session.",
    created_at: datetime.datetime | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> Passage:
    """Create a realistic Passage matching letta-client 1.7.6."""
    return Passage(
        text=text,
        id=passage_id,
        created_at=created_at or datetime.datetime(2024, 1, 15, 10, 30, 0),
        embedding=None,
        embedding_config=None,
        archive_id="archive-001",
        file_id=None,
        file_name=None,
        source_id=None,
        metadata=metadata,
        tags=tags,
        is_deleted=False,
        created_by_id=None,
        last_updated_by_id=None,
        updated_at=None,
    )


def make_exchange_passage(
    passage_id: str = "ex-001",
    user: str = "Fix the bug",
    assistant: str = "Done!",
    created_at: datetime.datetime | None = None,
    session_id: str = "sess-1",
    project: str = "myproject",
) -> Passage:
    """Create a Passage tagged as an exchange."""
    return make_passage(
        passage_id=passage_id,
        text=f"User: {user}\nAssistant: {assistant}",
        created_at=created_at,
        tags=["exchange"],
        metadata={"session_id": session_id, "project": project},
    )


def make_summary_passage(
    passage_id: str = "sum-001",
    text: str = "Session summary: worked on config fixes.",
    created_at: datetime.datetime | None = None,
    session_id: str = "sess-1",
    project: str = "myproject",
) -> Passage:
    """Create a Passage tagged as a summary."""
    return make_passage(
        passage_id=passage_id,
        text=text,
        created_at=created_at,
        tags=["summary"],
        metadata={"session_id": session_id, "project": project},
    )


# --- Fixtures ---


@pytest.fixture
def mock_letta() -> AsyncMock:
    """Create a fully-configured AsyncMock for AsyncLetta.

    All sub-resources are AsyncMock so their methods can be awaited.
    """
    client = AsyncMock()

    client.agents.blocks.retrieve.return_value = make_block_response()
    client.agents.blocks.update.return_value = make_block_response(value="Updated value")
    client.agents.blocks.list.return_value = make_coding_block_set()

    client.agents.passages.list.return_value = [
        make_exchange_passage(passage_id="p1", user="Fix the bug", assistant="Found and fixed the issue."),
        make_summary_passage(passage_id="p2", text="Session summary: fixed config loading bug."),
    ]
    client.agents.passages.create.return_value = [make_passage(text="Archived memory")]
    client.archives.passages.create.return_value = make_passage(text="Archived memory")

    return client


@pytest.fixture
def mock_sync_letta() -> MagicMock:
    """Create a fully-configured MagicMock for synchronous Letta client.

    Used by hooks (which use sync Letta client).
    """
    client = MagicMock()

    client.agents.blocks.list.return_value = make_coding_block_set()
    client.agents.passages.list.return_value = [
        make_exchange_passage(passage_id="p1"),
        make_summary_passage(passage_id="p2"),
    ]
    client.archives.passages.create.return_value = make_passage(text="Persisted")

    return client
