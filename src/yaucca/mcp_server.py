"""FastMCP server with cloud-backed memory tools for Claude Code.

Exposes 6 tools for interacting with yaucca's persistent memory system.
Runs as a stdio MCP server that Claude Code connects to via .mcp.json.
All calls proxy through the yaucca cloud HTTP API.

All logging goes to stderr (stdout is the JSON-RPC protocol channel).
"""

import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from yaucca.config import get_settings

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(name)s: %(message)s")
logger = logging.getLogger("yaucca.mcp")

# Module-level state, initialized during server lifespan
_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(server: FastMCP) -> Any:
    """Initialize HTTP client on startup."""
    global _client
    settings = get_settings()

    headers: dict[str, str] = {}
    if settings.cloud.auth_token:
        headers["Authorization"] = f"Bearer {settings.cloud.auth_token}"

    _client = httpx.AsyncClient(
        base_url=settings.cloud.url,
        headers=headers,
        timeout=30.0,
    )

    logger.info("Connected to yaucca cloud at %s", settings.cloud.url)
    yield
    await _client.aclose()
    _client = None
    logger.info("Shutting down yaucca MCP server")


mcp = FastMCP("yaucca", lifespan=lifespan)


@mcp.tool()
async def get_memory_block(block_name: str) -> str:
    """Get a core memory block by name (e.g. 'user', 'projects', 'patterns').

    Returns the current value of the specified memory block.
    """
    assert _client is not None
    resp = await _client.get(f"/api/blocks/{block_name}")
    if resp.status_code == 404:
        return f"Block '{block_name}' not found"
    resp.raise_for_status()
    return resp.json().get("value", "")


@mcp.tool()
async def update_memory_block(block_name: str, value: str) -> str:
    """Update a core memory block value.

    IMPORTANT: This replaces the entire block value. Read the block first,
    modify the content, then write the full updated value back.
    """
    assert _client is not None
    resp = await _client.put(f"/api/blocks/{block_name}", json={"value": value})
    if resp.status_code == 404:
        return f"Block '{block_name}' not found"
    resp.raise_for_status()
    return f"Updated memory block '{block_name}'"


@mcp.tool()
async def search_archival_memory(query: str, count: int = 10) -> str:
    """Search archival memory for past experiences and learnings.

    Uses semantic similarity search over all stored memories.
    Returns matching entries ranked by relevance.
    """
    assert _client is not None
    resp = await _client.get("/api/passages/search", params={"q": query, "top_k": count})
    resp.raise_for_status()
    entries = [{"text": p["text"], "id": p["id"]} for p in resp.json()]
    return str(entries)


@mcp.tool()
async def insert_archival_memory(text: str) -> str:
    """Store a new entry in archival memory.

    Use this for experiences, learnings, and insights that don't fit in core memory blocks.
    Entries are embedded for later semantic search.
    """
    assert _client is not None
    resp = await _client.post("/api/passages", json={"text": text})
    resp.raise_for_status()
    return "Memory archived successfully"


@mcp.tool()
async def list_memory_blocks() -> str:
    """List all available core memory blocks with their sizes."""
    assert _client is not None
    resp = await _client.get("/api/blocks")
    resp.raise_for_status()
    blocks = [{"label": b["label"], "value_length": len(b.get("value", ""))} for b in resp.json()]
    return str(blocks)


@mcp.tool()
async def get_recent_messages(count: int = 10) -> str:
    """Get recent conversation exchanges from recall memory."""
    assert _client is not None
    resp = await _client.get(
        "/api/passages",
        params={"tag": "exchange", "limit": count, "order": "desc"},
    )
    resp.raise_for_status()
    formatted = []
    for p in resp.json():
        entry: dict[str, str] = {"text": p.get("text", "")[:500], "id": p.get("id", "")}
        created = p.get("created_at")
        if created:
            entry["date"] = str(created)
        formatted.append(entry)
    return str(formatted)


if __name__ == "__main__":
    mcp.run(transport="stdio")
