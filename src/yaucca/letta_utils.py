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


def resolve_archive_id_from_list(archives: Any) -> str | None:
    """Extract archive_id from an archives list response.

    Uses the archives.list(agent_id=...) result, which returns archives
    attached to the agent. Returns the first archive's ID, or None.
    """
    items = archives.items if hasattr(archives, "items") else archives
    for archive in items:
        if hasattr(archive, "id"):
            result: str = archive.id
            return result
    return None
