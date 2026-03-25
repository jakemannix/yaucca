"""Remote MCP server for Claude.ai and Claude mobile access.

Creates a FastMCP instance with Streamable HTTP transport and OAuth 2.1.
Tools call the database directly (same process), not via HTTP proxy.
Mount the returned Starlette app on the existing FastAPI server.
"""

import logging
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from yaucca.cloud.oauth_provider import OAuthStore, YauccaOAuthProvider

logger = logging.getLogger("yaucca.cloud.mcp_remote")


def create_remote_mcp(
    issuer_url: str,
    oauth_store: OAuthStore,
    github_client_id: str = "",
    github_callback_url: str = "",
) -> FastMCP:
    """Create a remote MCP server with OAuth auth and direct-DB tools.

    Args:
        issuer_url: The public URL of this server (used as OAuth issuer).
        oauth_store: SQLite-backed OAuth state store.
        github_client_id: GitHub OAuth App client ID for authentication.
        github_callback_url: Callback URL for GitHub OAuth (on this server).
    """
    provider = YauccaOAuthProvider(oauth_store, github_client_id, github_callback_url)
    resource_url = f"{issuer_url.rstrip('/')}/mcp"

    # Allow the issuer's host in transport security (DNS rebinding protection).
    parsed = urlparse(issuer_url)
    allowed_host = parsed.netloc or parsed.hostname or "localhost"

    mcp = FastMCP(
        "yaucca",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(issuer_url),
            resource_server_url=AnyHttpUrl(resource_url),
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["memory:read", "memory:write"],
                default_scopes=["memory:read", "memory:write"],
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[allowed_host],
        ),
    )

    # --- Memory tools (call DB directly) ---

    @mcp.tool()
    async def get_memory_block(block_name: str) -> str:
        """Get a core memory block by name (e.g. 'user', 'projects', 'patterns').

        Returns the current value of the specified memory block.
        """
        from yaucca.cloud.server import _get_db

        block = _get_db().get_block(block_name)
        if not block:
            return f"Block '{block_name}' not found"
        return block.value

    @mcp.tool()
    async def update_memory_block(block_name: str, value: str) -> str:
        """Update a core memory block value.

        IMPORTANT: This replaces the entire block value. Read the block first,
        modify the content, then write the full updated value back.
        """
        from yaucca.cloud.server import _get_db

        block = _get_db().update_block(block_name, value)
        if not block:
            return f"Block '{block_name}' not found"
        return f"Updated memory block '{block_name}'"

    @mcp.tool()
    async def search_archival_memory(query: str, count: int = 10, max_chars: int = 2000) -> str:
        """Search archival memory for past experiences and learnings.

        Uses semantic similarity search over all stored memories.
        Returns matching entries ranked by relevance.

        Args:
            query: Semantic search query.
            count: Number of results to return.
            max_chars: Max characters per result text (0 = no limit). Default 2000
                       keeps results compact; increase for full passage retrieval.
        """
        from yaucca.cloud.server import _get_db, _get_embedder

        db = _get_db()
        if not db.has_vec:
            return "Vector search unavailable"
        embedder = _get_embedder()
        embedding = await embedder.embed(query)
        passages = db.search_passages(embedding, top_k=count)
        entries = []
        for p in passages:
            text = p.text
            if max_chars and len(text) > max_chars:
                text = text[:max_chars] + f"... [{len(p.text)} chars total]"
            entry: dict[str, object] = {"text": text, "id": p.id}
            if p.tags:
                entry["tags"] = p.tags
            entries.append(entry)
        return str(entries)

    @mcp.tool()
    async def get_passages(ids: list[str], max_chars: int = 0, offset: int = 0) -> str:
        """Fetch full text of specific passages by ID.

        Use this after search_archival_memory to drill into interesting results.
        Search returns truncated previews; this returns full (or windowed) text.

        Args:
            ids: List of passage IDs to fetch.
            max_chars: Max characters per passage (0 = no limit).
            offset: Character offset to start reading from (for paging through long passages).
        """
        from yaucca.cloud.server import _get_db

        db = _get_db()
        results = []
        for pid in ids:
            passage = db.get_passage(pid)
            if not passage:
                results.append({"id": pid, "error": "not found"})
                continue
            text = passage.text
            total = len(text)
            if offset:
                text = text[offset:]
            if max_chars and len(text) > max_chars:
                text = text[:max_chars] + f"... [{total} chars total]"
            entry: dict[str, object] = {"id": passage.id, "text": text, "total_chars": total}
            if passage.tags:
                entry["tags"] = passage.tags
            results.append(entry)
        return str(results)

    @mcp.tool()
    async def insert_archival_memory(text: str, tags: list[str] | None = None) -> str:
        """Store a new entry in archival memory.

        Use this for experiences, learnings, and insights that don't fit in core memory blocks.
        Entries are embedded for later semantic search.

        Args:
            text: The content to store.
            tags: Optional tags for classification (e.g. ["@inbox", "project:foo"]).
        """
        from yaucca.cloud.server import _get_db, _get_embed_queue

        db = _get_db()
        passage = db.create_passage(text=text, tags=tags)
        # Queue async embedding (same pattern as the REST API)
        try:
            eq = _get_embed_queue()
            eq.enqueue(passage.id, text)
        except Exception:
            logger.warning("Failed to queue embedding for passage %s", passage.id)
        return "Memory archived successfully"

    @mcp.tool()
    async def list_memory_blocks() -> str:
        """List all available core memory blocks with their sizes."""
        from yaucca.cloud.server import _get_db

        blocks = _get_db().list_blocks()
        return str([{"label": b.label, "value_length": len(b.value)} for b in blocks])

    @mcp.tool()
    async def list_passages_by_tag(tag: str, limit: int = 50) -> str:
        """List archival passages that have a specific tag.

        Useful for retrieving all items with a given classification,
        e.g. all "@inbox" items, all "@next" actions, or all items
        in a project ("project:yaucca-v3").

        Args:
            tag: The tag to filter by (e.g. "@inbox", "@next", "project:foo").
            limit: Max number of results (default 50).
        """
        from yaucca.cloud.server import _get_db

        passages = _get_db().list_passages(tag=tag, limit=limit)
        results = []
        for p in passages:
            entry: dict[str, object] = {"id": p.id, "text": p.text, "tags": p.tags}
            if p.created_at:
                entry["created_at"] = p.created_at
            results.append(entry)
        return str(results)

    @mcp.tool()
    async def update_passage_tags(passage_id: str, tags: list[str]) -> str:
        """Update the tags on an existing archival passage.

        Replaces all tags on the passage. Read current tags first if you
        need to add/remove individual tags.

        Args:
            passage_id: The passage ID to update.
            tags: The new complete list of tags.
        """
        from yaucca.cloud.server import _get_db

        db = _get_db()
        passage = db.get_passage(passage_id)
        if not passage:
            return f"Passage '{passage_id}' not found"
        db.update_passage_tags(passage_id, tags)
        return f"Updated tags on passage '{passage_id}'"

    @mcp.tool()
    async def get_recent_messages(count: int = 10) -> str:
        """Get recent conversation exchanges from recall memory."""
        from yaucca.cloud.server import _get_db

        passages = _get_db().list_passages(tag="exchange", limit=count, order="desc")
        formatted = []
        for p in passages:
            entry: dict[str, str] = {"text": p.text[:500], "id": p.id}
            if p.created_at:
                entry["date"] = p.created_at
            formatted.append(entry)
        return str(formatted)

    # Expose the provider so the GitHub callback endpoint can access it.
    mcp._yaucca_oauth_provider = provider  # type: ignore[attr-defined]

    return mcp
