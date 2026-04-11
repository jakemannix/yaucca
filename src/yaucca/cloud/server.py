"""FastAPI HTTP server for yaucca cloud.

Serves the REST API for memory blocks and archival passages.
Both the local MCP server/hooks and the remote MCP transport call this API.

Passage writes are immediate (text stored in SQLite), with embeddings
computed asynchronously in a background queue that batches API calls.
"""

import logging
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from yaucca.cloud.backfill import backfill_all_profiles, backfill_profile
from yaucca.cloud.db import Database
from yaucca.cloud.embed import Embedder, OpenAICompatibleEmbedder, StubEmbedder
from yaucca.cloud.embed_queue import EmbeddingQueue

logger = logging.getLogger("yaucca.cloud.server")

# Module-level state set during lifespan
_db: Database | None = None
_embedder: Embedder | None = None
_embed_queue: EmbeddingQueue | None = None


def _get_db() -> Database:
    assert _db is not None
    return _db


def _get_embedder() -> Embedder:
    assert _embedder is not None
    return _embedder


def _get_embed_queue() -> EmbeddingQueue:
    assert _embed_queue is not None
    return _embed_queue


# --- Request/response models ---


class BlockUpdate(BaseModel):
    value: str


class PassageCreate(BaseModel):
    text: str
    tags: list[str] | None = None
    metadata: dict[str, str] | None = None


# --- Auth ---


def _verify_token(request: Request) -> None:
    """Simple bearer token auth. Skipped if YAUCCA_AUTH_TOKEN is not set."""
    expected = os.environ.get("YAUCCA_AUTH_TOKEN")
    if not expected:
        return
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _resolve_exclude_tags(explicit: str | None) -> list[str]:
    """Resolve exclude_tags: use explicit value if given, else server default.

    Args:
        explicit: Comma-separated tags from the request, or None for default.
    Returns:
        List of tags to exclude (may be empty).
    """
    if explicit is not None:
        return [t.strip() for t in explicit.split(",") if t.strip()]
    default = os.environ.get("YAUCCA_DEFAULT_EXCLUDE_TAGS", "")
    return [t.strip() for t in default.split(",") if t.strip()]


# --- App factory ---


def create_app(
    db_path: str = ":memory:",
    on_write: Any = None,
    commit_fn: Callable[[], None] | None = None,
    on_db_ready: Callable[[], None] | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    Args:
        db_path: Path to SQLite database file.
        on_write: Optional callback after DB writes (e.g., for non-embed writes like blocks).
        commit_fn: Optional callback for the embedding queue to commit after batch flush.
                   If None, falls back to on_write.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        global _db, _embedder, _embed_queue

        db = Database(db_path=db_path, on_write=on_write)
        db.connect()
        db.init_default_blocks()
        _db = db

        # Embedding provider: check for OpenRouter key first, then OpenAI, then stub
        embed_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        embed_base_url = os.environ.get("YAUCCA_EMBED_BASE_URL", "https://openrouter.ai/api/v1")
        embed_model = os.environ.get("YAUCCA_EMBED_MODEL", "qwen/qwen3-embedding-8b")
        embed_dims = int(os.environ.get("YAUCCA_EMBED_DIMS", "1024"))
        if embed_key:
            _embedder = OpenAICompatibleEmbedder(
                api_key=embed_key,
                base_url=embed_base_url,
                model=embed_model,
                dimensions=embed_dims,
            )
            logger.info("Using embeddings: %s @ %s (%d dims)", embed_model, embed_base_url, embed_dims)
        else:
            _embedder = StubEmbedder()
            logger.info("Using stub embeddings (no OPENROUTER_API_KEY or OPENAI_API_KEY)")

        # Embedding queue: batches embedding + commit in background
        _embed_queue = EmbeddingQueue(
            db=db,
            embedder=_embedder,
            commit_fn=commit_fn or on_write,
            max_batch_size=50,
            max_wait_seconds=5.0,
        )
        await _embed_queue.start()

        if on_db_ready:
            on_db_ready()

        logger.info("yaucca cloud server started (db=%s)", db_path)
        yield

        await _embed_queue.stop()
        db.close()
        _db = None
        _embedder = None
        _embed_queue = None

    app = FastAPI(title="yaucca", lifespan=lifespan)

    # --- Health ---

    @app.get("/health")
    async def health(db: Database = Depends(_get_db)) -> dict[str, Any]:
        return {
            "status": "ok",
            "vec_enabled": db.has_vec,
            "vec_profiles": [p.name for p in db.active_profiles],
            "embed_queue_pending": _embed_queue.pending if _embed_queue else 0,
        }

    # --- Block endpoints ---

    @app.get("/api/blocks", dependencies=[Depends(_verify_token)])
    async def list_blocks(db: Database = Depends(_get_db)) -> list[dict[str, Any]]:
        blocks = db.list_blocks()
        return [
            {
                "label": b.label,
                "value": b.value,
                "description": b.description,
                "limit": b.char_limit,
                "updated_at": b.updated_at,
            }
            for b in blocks
        ]

    @app.get("/api/blocks/{label}", dependencies=[Depends(_verify_token)])
    async def get_block(label: str, db: Database = Depends(_get_db)) -> dict[str, Any]:
        block = db.get_block(label)
        if not block:
            raise HTTPException(status_code=404, detail=f"Block '{label}' not found")
        return {
            "label": block.label,
            "value": block.value,
            "description": block.description,
            "limit": block.char_limit,
            "updated_at": block.updated_at,
        }

    @app.put("/api/blocks/{label}", dependencies=[Depends(_verify_token)])
    async def update_block(label: str, body: BlockUpdate, db: Database = Depends(_get_db)) -> dict[str, Any]:
        block = db.get_block(label)
        if not block:
            raise HTTPException(status_code=404, detail=f"Block '{label}' not found")
        if len(body.value) > block.char_limit:
            raise HTTPException(
                status_code=400,
                detail=f"Value exceeds char limit ({len(body.value)} > {block.char_limit})",
            )
        updated = db.update_block(label, body.value)
        assert updated is not None
        return {
            "label": updated.label,
            "value": updated.value,
            "description": updated.description,
            "limit": updated.char_limit,
            "updated_at": updated.updated_at,
        }

    # --- Passage endpoints ---

    @app.get("/api/passages", dependencies=[Depends(_verify_token)])
    async def list_passages(
        tag: str | None = None,
        search: str | None = None,
        limit: int = 50,
        order: str = "desc",
        exclude_tags: str | None = None,
        db: Database = Depends(_get_db),
    ) -> dict[str, Any]:
        etags = _resolve_exclude_tags(exclude_tags)
        passages = db.list_passages(tag=tag, search=search, limit=limit, order=order, exclude_tags=etags or None)
        result: dict[str, Any] = {
            "passages": [
                {
                    "id": p.id,
                    "text": p.text,
                    "tags": p.tags,
                    "metadata": p.metadata,
                    "created_at": p.created_at,
                }
                for p in passages
            ],
        }
        if etags:
            result["excluded_tags"] = etags
        return result

    @app.post("/api/passages", dependencies=[Depends(_verify_token)], status_code=201)
    async def create_passage(
        body: PassageCreate,
        db: Database = Depends(_get_db),
        eq: EmbeddingQueue = Depends(_get_embed_queue),
    ) -> dict[str, Any]:
        # Write text to SQLite immediately (no embedding yet)
        passage = db.create_passage(
            text=body.text,
            tags=body.tags,
            metadata=body.metadata,
        )
        # Enqueue for async background embedding
        await eq.enqueue(passage.id, body.text)
        return {
            "id": passage.id,
            "text": passage.text,
            "tags": passage.tags,
            "metadata": passage.metadata,
            "created_at": passage.created_at,
        }

    @app.delete("/api/passages/{passage_id}", dependencies=[Depends(_verify_token)])
    async def delete_passage(passage_id: str, db: Database = Depends(_get_db)) -> JSONResponse:
        deleted = db.delete_passage(passage_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Passage not found")
        return JSONResponse(content={"deleted": True})

    @app.get("/api/passages/search", dependencies=[Depends(_verify_token)])
    async def search_passages(
        q: str,
        top_k: int = 10,
        profile: str | None = None,
        exclude_tags: str | None = None,
        db: Database = Depends(_get_db),
        embedder: Embedder = Depends(_get_embedder),
    ) -> dict[str, Any]:
        # Search embeds the query inline — single call, ~500ms
        if not db.has_vec:
            raise HTTPException(status_code=503, detail="Vector search unavailable: sqlite-vec not loaded")
        etags = _resolve_exclude_tags(exclude_tags)
        embedding = await embedder.embed(q)
        passages = db.search_passages(embedding, top_k=top_k, profile_name=profile, exclude_tags=etags or None)
        result: dict[str, Any] = {
            "passages": [
                {
                    "id": p.id,
                    "text": p.text,
                    "tags": p.tags,
                    "metadata": p.metadata,
                    "created_at": p.created_at,
                }
                for p in passages
            ],
        }
        if etags:
            result["excluded_tags"] = etags
        return result

    @app.get("/api/passages/{passage_id}", dependencies=[Depends(_verify_token)])
    async def get_passage(passage_id: str, db: Database = Depends(_get_db)) -> dict[str, Any]:
        passage = db.get_passage(passage_id)
        if not passage:
            raise HTTPException(status_code=404, detail="Passage not found")
        return {
            "id": passage.id,
            "text": passage.text,
            "tags": passage.tags,
            "metadata": passage.metadata,
            "created_at": passage.created_at,
        }

    # --- Admin endpoints ---

    @app.get("/api/admin/diagnostics", dependencies=[Depends(_verify_token)])
    async def admin_diagnostics(
        db: Database = Depends(_get_db),
        embedder: Embedder = Depends(_get_embedder),
        eq: EmbeddingQueue = Depends(_get_embed_queue),
    ) -> dict[str, Any]:
        """Run timed operations from inside the container to diagnose latency."""
        import httpx as httpx_diag

        results: dict[str, Any] = {}

        # 1. Raw HTTP connectivity to OpenRouter
        t0 = time.monotonic()
        try:
            async with httpx_diag.AsyncClient(timeout=15.0) as hc:
                resp = await hc.get("https://openrouter.ai/api/v1/models",
                                    headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}"})
                results["openrouter_connectivity_ms"] = round((time.monotonic() - t0) * 1000)
        except Exception as e:
            results["openrouter_connectivity_ms"] = round((time.monotonic() - t0) * 1000)
            results["openrouter_error"] = str(e)

        # 2. Single embedding call
        t0 = time.monotonic()
        try:
            emb = await embedder.embed("diagnostic test")
            results["embed_single_ms"] = round((time.monotonic() - t0) * 1000)
        except Exception as e:
            results["embed_single_ms"] = round((time.monotonic() - t0) * 1000)
            results["embed_error"] = str(e)

        # 3. Batch embedding (5 items)
        t0 = time.monotonic()
        try:
            await embedder.embed_batch(["test one", "test two", "test three", "test four", "test five"])
            results["embed_batch_5_ms"] = round((time.monotonic() - t0) * 1000)
        except Exception as e:
            results["embed_batch_5_ms"] = round((time.monotonic() - t0) * 1000)
            results["embed_batch_error"] = str(e)

        # 4. Passage create (async embed via queue)
        t0 = time.monotonic()
        p = db.create_passage(text="diagnostics timing test", tags=["diagnostics"])
        results["passage_write_ms"] = round((time.monotonic() - t0) * 1000)

        # Clean up
        db.delete_passage(p.id)

        # 5. Queue status
        results["embed_queue_pending"] = eq.pending

        return results

    @app.post("/api/admin/backfill", dependencies=[Depends(_verify_token)])
    async def admin_backfill(
        profile: str | None = None,
        db: Database = Depends(_get_db),
        embedder: Embedder = Depends(_get_embedder),
    ) -> dict[str, Any]:
        """Backfill embedding profiles for passages missing from vec tables."""
        if profile:
            result = await backfill_profile(db, embedder, profile)
            return {profile: result}
        else:
            return await backfill_all_profiles(db, embedder)

    return app


def create_composite_app(
    db_path: str = ":memory:",
    on_write: Any = None,
    commit_fn: Callable[[], None] | None = None,
    issuer_url: str | None = None,
) -> FastAPI:
    """Create composite app with REST API + remote MCP endpoint.

    If issuer_url is provided, mounts the remote MCP server with OAuth at /mcp.
    The MCP tools call the database directly (same process).
    """
    if not issuer_url:
        return create_app(db_path, on_write, commit_fn)

    import httpx as httpx_oauth

    from yaucca.cloud.mcp_remote import create_remote_mcp
    from yaucca.cloud.oauth_provider import OAuthStore

    github_client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    github_client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "")
    github_allowed_users = os.environ.get("GITHUB_ALLOWED_USERS", "jakemannix").split(",")
    github_callback_url = f"{issuer_url.rstrip('/')}/oauth/github/callback"

    # OAuth store shares the same SQLite connection as the main DB.
    oauth_store = OAuthStore(lambda: _get_db().conn)

    # Create the MCP server and get its Starlette app + session manager.
    mcp = create_remote_mcp(
        issuer_url, oauth_store,
        github_client_id=github_client_id,
        github_callback_url=github_callback_url,
    )
    mcp_starlette = mcp.streamable_http_app()
    session_manager = mcp.session_manager
    oauth_provider = mcp._yaucca_oauth_provider  # type: ignore[attr-defined]

    app = create_app(db_path, on_write, commit_fn, on_db_ready=oauth_store.init_schema)

    # --- GitHub OAuth callback ---

    @app.get("/oauth/github/callback")
    async def github_callback(code: str, state: str) -> Any:
        """Handle GitHub OAuth callback: verify user, complete MCP authorization."""
        from fastapi.responses import RedirectResponse

        # Exchange GitHub code for access token
        async with httpx_oauth.AsyncClient() as hc:
            token_resp = await hc.post(
                "https://github.com/login/oauth/access_token",
                json={
                    "client_id": github_client_id,
                    "client_secret": github_client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            if token_resp.status_code != 200:
                raise HTTPException(status_code=502, detail="GitHub token exchange failed")
            github_token = token_resp.json().get("access_token")
            if not github_token:
                raise HTTPException(status_code=400, detail=f"GitHub error: {token_resp.json().get('error_description', 'no token')}")

            # Get GitHub user info
            user_resp = await hc.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {github_token}", "Accept": "application/json"},
            )
            if user_resp.status_code != 200:
                raise HTTPException(status_code=502, detail="GitHub user info failed")
            github_username = user_resp.json().get("login", "")

        # Check allowlist
        if github_username not in github_allowed_users:
            logger.warning("GitHub user '%s' not in allowed list: %s", github_username, github_allowed_users)
            raise HTTPException(status_code=403, detail=f"User '{github_username}' is not authorized")

        # Complete the MCP OAuth flow
        redirect_url = oauth_provider.complete_authorization(state, github_username)
        if not redirect_url:
            raise HTTPException(status_code=400, detail="Authorization request expired or invalid")

        logger.info("GitHub user '%s' authorized for MCP access", github_username)
        return RedirectResponse(url=redirect_url)

    # Wrap the existing lifespan to also run the MCP session manager.
    _original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _composite_lifespan(a: FastAPI):  # type: ignore[no-untyped-def]
        async with _original_lifespan(a) as state, session_manager.run():
            logger.info("MCP session manager started")
            yield state

    app.router.lifespan_context = _composite_lifespan

    # Mount the MCP Starlette app for /mcp and OAuth well-known endpoints.
    app.mount("/", mcp_starlette)
    logger.info("Mounted remote MCP server at /mcp (issuer: %s)", issuer_url)

    return app
