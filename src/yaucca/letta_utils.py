"""Shared utilities for Letta API interactions.

Functions used by both the sync hooks and async MCP server.
"""

from typing import Any


def extract_archive_id(passages: list[Any]) -> str | None:
    """Extract archive_id from the first passage in a list.

    Works with both sync and async Letta API passage objects.
    Returns None if the list is empty or the passage has no archive_id.
    """
    if passages and hasattr(passages[0], "archive_id"):
        result: str = passages[0].archive_id
        return result
    return None
