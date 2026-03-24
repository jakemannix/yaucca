"""Shared test fixtures and factory functions for yaucca tests.

Provides mock objects and helpers for testing the cloud-backed MCP tools,
hooks, and storage layer.
"""

import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

# --- Simple data objects for prompt.py rendering ---


class MockBlock:
    """Mimics the block interface expected by prompt.py."""

    def __init__(
        self,
        label: str = "user",
        value: str = "The user prefers Python and async patterns.",
        limit: int = 5000,
        description: str = "Core memory block",
    ) -> None:
        self.label = label
        self.value = value
        self.limit = limit
        self.description = description


class MockPassage:
    """Mimics the passage interface expected by prompt.py."""

    def __init__(
        self,
        text: str = "A memory about a coding session.",
        passage_id: str = "passage-789",
        created_at: datetime.datetime | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.text = text
        self.id = passage_id
        self.created_at = created_at or datetime.datetime(2024, 1, 15, 10, 30, 0)
        self.tags = tags or []
        self.metadata = metadata or {}


# --- Factory functions ---


def make_block(
    label: str = "user",
    value: str = "The user prefers Python and async patterns.",
    limit: int = 5000,
    description: str = "Core memory block",
) -> MockBlock:
    return MockBlock(label=label, value=value, limit=limit, description=description)


def make_coding_block_set() -> list[MockBlock]:
    """Create the 5-block set for a coding-focused agent."""
    return [
        make_block(
            label="user",
            value="Jake Mannix, Technical Fellow at Walmart. Prefers Python, async, uv.",
            limit=5000,
            description="Information about the user — preferences, projects, work style",
        ),
        make_block(
            label="projects",
            value="Nameless agent: stateful AI agent with Claude SDK + Letta. yaucca: memory for Claude Code.",
            limit=10000,
            description="Active projects, repos, and goals being worked on",
        ),
        make_block(
            label="patterns",
            value="Uses ruff for linting, pytest for tests, hatchling for builds.",
            limit=10000,
            description="Recurring patterns, conventions, preferred tools and approaches",
        ),
        make_block(
            label="learnings",
            value="archives.passages.create bypasses Letta LLM loop — always prefer it.",
            limit=10000,
            description="Hard-won insights, debugging lessons, things that worked or didn't",
        ),
        make_block(
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
) -> MockPassage:
    return MockPassage(text=text, passage_id=passage_id, created_at=created_at, tags=tags, metadata=metadata)


def make_exchange_passage(
    passage_id: str = "ex-001",
    user: str = "Fix the bug",
    assistant: str = "Done!",
    created_at: datetime.datetime | None = None,
    session_id: str = "sess-1",
    project: str = "myproject",
) -> MockPassage:
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
) -> MockPassage:
    return make_passage(
        passage_id=passage_id,
        text=text,
        created_at=created_at,
        tags=["summary"],
        metadata={"session_id": session_id, "project": project},
    )


# --- Cloud API response helpers ---


def make_block_dict(
    label: str = "user",
    value: str = "Test value",
    description: str = "Core memory block",
    limit: int = 5000,
) -> dict[str, Any]:
    """Create a dict matching the cloud API block response shape."""
    return {
        "label": label,
        "value": value,
        "description": description,
        "limit": limit,
        "updated_at": "2024-01-15T10:30:00",
    }


def make_passage_dict(
    passage_id: str = "passage-789",
    text: str = "A memory about a coding session.",
    tags: list[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a dict matching the cloud API passage response shape."""
    return {
        "id": passage_id,
        "text": text,
        "tags": tags or [],
        "metadata": metadata or {},
        "created_at": "2024-01-15T10:30:00",
    }


# --- Fixtures ---


@pytest.fixture
def mock_async_client() -> AsyncMock:
    """Create a mock httpx.AsyncClient for MCP server tests."""
    return AsyncMock()
