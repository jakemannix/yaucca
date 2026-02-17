"""FastMCP server with Letta memory tools for Claude Code.

Exposes 6 tools for interacting with Letta's persistent memory system.
Runs as a stdio MCP server that Claude Code connects to via .mcp.json.

All logging goes to stderr (stdout is the JSON-RPC protocol channel).
"""

import logging
import sys
import warnings
from contextlib import asynccontextmanager
from typing import Any

from letta_client import AsyncLetta
from mcp.server.fastmcp import FastMCP

from yaucca.config import get_settings
from yaucca.letta_utils import extract_archive_id

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(name)s: %(message)s")
logger = logging.getLogger("yaucca.mcp")

# Module-level state, initialized during server lifespan
_letta: AsyncLetta | None = None
_agent_id: str | None = None
_archive_id: str | None = None


@asynccontextmanager
async def lifespan(server: FastMCP) -> Any:
    """Initialize Letta client on startup."""
    global _letta, _agent_id
    settings = get_settings()

    kwargs: dict[str, Any] = {"base_url": settings.letta.base_url}
    if settings.letta.api_key:
        kwargs["token"] = settings.letta.api_key
    _letta = AsyncLetta(**kwargs)

    _agent_id = settings.agent.agent_id
    if not _agent_id:
        logger.error("YAUCCA_AGENT_ID not set")
        sys.exit(1)

    logger.info("Connected to Letta at %s, agent=%s", settings.letta.base_url, _agent_id)
    yield
    logger.info("Shutting down yaucca MCP server")


mcp = FastMCP("yaucca", lifespan=lifespan)


async def _resolve_archive_id() -> str | None:
    """Lazily resolve and cache the agent's archive ID."""
    global _archive_id
    if _archive_id:
        return _archive_id
    if not _letta or not _agent_id:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            passages = await _letta.agents.passages.list(_agent_id, limit=1)
        _archive_id = extract_archive_id(passages)
    except Exception:
        pass
    return _archive_id


@mcp.tool()
async def get_memory_block(block_name: str) -> str:
    """Get a core memory block by name (e.g. 'user', 'projects', 'patterns').

    Returns the current value of the specified memory block.
    """
    assert _letta and _agent_id
    block = await _letta.agents.blocks.retrieve(block_name, agent_id=_agent_id)
    return block.value or ""


@mcp.tool()
async def update_memory_block(block_name: str, value: str) -> str:
    """Update a core memory block value.

    IMPORTANT: This replaces the entire block value. Read the block first,
    modify the content, then write the full updated value back.
    """
    assert _letta and _agent_id
    await _letta.agents.blocks.update(block_name, agent_id=_agent_id, value=value)
    return f"Updated memory block '{block_name}'"


@mcp.tool()
async def search_archival_memory(query: str, count: int = 10) -> str:
    """Search archival memory for past experiences and learnings.

    Uses semantic similarity search over all stored memories.
    Returns matching entries ranked by relevance.
    """
    assert _letta and _agent_id

    # Try semantic search first (Letta server >= 0.14+)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            result = await _letta.agents.passages.search(
                _agent_id,
                query=query,
                top_k=count,
            )
        entries = [{"text": r.content, "id": r.id} for r in result.results]
        return str(entries)
    except Exception:
        pass

    # Fallback: text-based substring search
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        results = await _letta.agents.passages.list(_agent_id, search=query, limit=count)
    entries = [{"text": r.text, "id": r.id} for r in results]
    return str(entries)


@mcp.tool()
async def insert_archival_memory(text: str) -> str:
    """Store a new entry in archival memory.

    Use this for experiences, learnings, and insights that don't fit in core memory blocks.
    Entries are embedded for later semantic search.
    """
    assert _letta and _agent_id
    archive_id = await _resolve_archive_id()
    if archive_id:
        await _letta.archives.passages.create(archive_id, text=text)
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            await _letta.agents.passages.create(_agent_id, text=text)
    return "Memory archived successfully"


@mcp.tool()
async def list_memory_blocks() -> str:
    """List all available core memory blocks with their sizes."""
    assert _letta and _agent_id
    blocks_page = await _letta.agents.blocks.list(_agent_id)
    block_items = blocks_page.items if hasattr(blocks_page, "items") else blocks_page
    blocks = [{"label": b.label, "value_length": len(b.value) if b.value else 0} for b in block_items]
    return str(blocks)


@mcp.tool()
async def get_recent_messages(count: int = 10) -> str:
    """Get recent conversation exchanges from recall memory."""
    assert _letta and _agent_id
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        passages = await _letta.agents.passages.list(_agent_id, limit=count * 2, ascending=False)
    exchanges = [p for p in passages if "exchange" in (getattr(p, "tags", None) or [])]
    formatted = []
    for p in exchanges[:count]:
        entry: dict[str, str] = {"text": (getattr(p, "text", "") or "")[:500], "id": getattr(p, "id", "")}
        created = getattr(p, "created_at", None)
        if created:
            entry["date"] = str(created)
        formatted.append(entry)
    return str(formatted)


if __name__ == "__main__":
    mcp.run(transport="stdio")
