"""FastAPI HTTP server for yaucca cloud.

Serves the REST API for memory blocks and archival passages.
Both the local MCP server/hooks and the remote MCP transport call this API.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from yaucca.cloud.db import Database
from yaucca.cloud.embed import Embedder, OpenAIEmbedder, StubEmbedder

logger = logging.getLogger("yaucca.cloud.server")

# Module-level state set during lifespan
_db: Database | None = None
_embedder: Embedder | None = None


def _get_db() -> Database:
    assert _db is not None
    return _db


def _get_embedder() -> Embedder:
    assert _embedder is not None
    return _embedder


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


# --- App factory ---


def create_app(db_path: str = ":memory:", on_write: Any = None) -> FastAPI:
    """Create the FastAPI application.

    Args:
        db_path: Path to SQLite database file.
        on_write: Optional callback after writes (e.g., volume.commit).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        global _db, _embedder

        db = Database(db_path=db_path, on_write=on_write)
        db.connect()
        db.init_default_blocks()
        _db = db

        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            _embedder = OpenAIEmbedder(api_key=openai_key)
            logger.info("Using OpenAI embeddings")
        else:
            _embedder = StubEmbedder()
            logger.info("Using stub embeddings (no OPENAI_API_KEY)")

        logger.info("yaucca cloud server started (db=%s)", db_path)
        yield

        db.close()
        _db = None
        _embedder = None

    app = FastAPI(title="yaucca", lifespan=lifespan)

    # --- Health ---

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

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
        db: Database = Depends(_get_db),
    ) -> list[dict[str, Any]]:
        passages = db.list_passages(tag=tag, search=search, limit=limit, order=order)
        return [
            {
                "id": p.id,
                "text": p.text,
                "tags": p.tags,
                "metadata": p.metadata,
                "created_at": p.created_at,
            }
            for p in passages
        ]

    @app.post("/api/passages", dependencies=[Depends(_verify_token)], status_code=201)
    async def create_passage(
        body: PassageCreate,
        db: Database = Depends(_get_db),
        embedder: Embedder = Depends(_get_embedder),
    ) -> dict[str, Any]:
        embedding = await embedder.embed(body.text)
        passage = db.create_passage(
            text=body.text,
            tags=body.tags,
            metadata=body.metadata,
            embedding=embedding,
        )
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
        db: Database = Depends(_get_db),
        embedder: Embedder = Depends(_get_embedder),
    ) -> list[dict[str, Any]]:
        embedding = await embedder.embed(q)
        if not db.has_vec:
            # Fallback to text search when sqlite-vec unavailable
            passages = db.list_passages(search=q, limit=top_k)
        else:
            passages = db.search_passages(embedding, top_k=top_k)
        return [
            {
                "id": p.id,
                "text": p.text,
                "tags": p.tags,
                "metadata": p.metadata,
                "created_at": p.created_at,
            }
            for p in passages
        ]

    return app
