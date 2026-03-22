"""SQLite + sqlite-vec storage layer for yaucca.

Provides persistent storage for memory blocks and archival passages with
vector similarity search. Transport-agnostic — the caller (Modal app or
local dev server) handles volume commits.
"""

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Block:
    """A core memory block."""

    label: str
    value: str
    description: str
    char_limit: int = 5000
    updated_at: str = ""


@dataclass
class Passage:
    """An archival passage with optional embedding."""

    id: str
    text: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    created_at: str = ""


class Database:
    """SQLite storage with optional sqlite-vec for vector search.

    Args:
        db_path: Path to SQLite database file, or ":memory:" for testing.
        on_write: Optional callback invoked after any write operation.
                  Used by Modal to trigger volume.commit().
    """

    def __init__(self, db_path: str = ":memory:", on_write: Callable[[], None] | None = None) -> None:
        self._db_path = db_path
        self._on_write = on_write
        self._conn: sqlite3.Connection | None = None
        self._has_vec = False

    def connect(self) -> None:
        """Open the database connection and initialize schema."""
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()
        self._try_load_vec()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    def _notify_write(self) -> None:
        if self._on_write:
            self._on_write()

    def _init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS blocks (
                label       TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                value       TEXT NOT NULL DEFAULT '',
                char_limit  INTEGER NOT NULL DEFAULT 5000,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS passages (
                id          TEXT PRIMARY KEY,
                text        TEXT NOT NULL,
                tags        TEXT DEFAULT '[]',
                metadata    TEXT DEFAULT '{}',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

    def _try_load_vec(self) -> None:
        """Try to load sqlite-vec extension for vector search."""
        try:
            import sqlite_vec

            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            # Create vector table if it doesn't exist
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS passages_vec USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding FLOAT[1536]
                )
            """)
            self._has_vec = True
        except (ImportError, Exception):
            self._has_vec = False

    @property
    def has_vec(self) -> bool:
        return self._has_vec

    # --- Block operations ---

    def init_default_blocks(self) -> None:
        """Create the 5 default memory blocks if they don't exist."""
        defaults = [
            ("user", "Information about the user — preferences, projects, work style", "", 5000),
            ("projects", "Active projects, repos, and goals being worked on", "", 10000),
            ("patterns", "Recurring patterns, conventions, preferred tools and approaches", "", 10000),
            ("learnings", "Hard-won insights, debugging lessons, things that worked or didn't", "", 10000),
            ("context", "Current session context — what we're working on, recent decisions", "", 5000),
        ]
        for label, desc, value, limit in defaults:
            self.conn.execute(
                "INSERT OR IGNORE INTO blocks (label, description, value, char_limit) VALUES (?, ?, ?, ?)",
                (label, desc, value, limit),
            )
        self.conn.commit()
        self._notify_write()

    def list_blocks(self) -> list[Block]:
        rows = self.conn.execute("SELECT label, value, description, char_limit, updated_at FROM blocks").fetchall()
        return [Block(label=r["label"], value=r["value"], description=r["description"], char_limit=r["char_limit"], updated_at=r["updated_at"] or "") for r in rows]

    def get_block(self, label: str) -> Block | None:
        row = self.conn.execute(
            "SELECT label, value, description, char_limit, updated_at FROM blocks WHERE label = ?", (label,)
        ).fetchone()
        if not row:
            return None
        return Block(label=row["label"], value=row["value"], description=row["description"], char_limit=row["char_limit"], updated_at=row["updated_at"] or "")

    def update_block(self, label: str, value: str) -> Block | None:
        self.conn.execute(
            "UPDATE blocks SET value = ?, updated_at = ? WHERE label = ?",
            (value, datetime.now(UTC).isoformat(), label),
        )
        self.conn.commit()
        self._notify_write()
        return self.get_block(label)

    # --- Passage operations ---

    def create_passage(
        self,
        text: str,
        tags: list[str] | None = None,
        metadata: dict[str, str] | None = None,
        embedding: list[float] | None = None,
    ) -> Passage:
        passage_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            "INSERT INTO passages (id, text, tags, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (passage_id, text, json.dumps(tags or []), json.dumps(metadata or {}), now),
        )
        if embedding and self._has_vec:
            self._store_embedding(passage_id, embedding)
        self.conn.commit()
        self._notify_write()
        return Passage(id=passage_id, text=text, tags=tags or [], metadata=metadata or {}, created_at=now)

    def get_passage(self, passage_id: str) -> Passage | None:
        row = self.conn.execute(
            "SELECT id, text, tags, metadata, created_at FROM passages WHERE id = ?", (passage_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_passage(row)

    def delete_passage(self, passage_id: str) -> bool:
        cursor = self.conn.execute("DELETE FROM passages WHERE id = ?", (passage_id,))
        if self._has_vec:
            self.conn.execute("DELETE FROM passages_vec WHERE id = ?", (passage_id,))
        self.conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            self._notify_write()
        return deleted

    def list_passages(
        self,
        tag: str | None = None,
        search: str | None = None,
        limit: int = 50,
        order: str = "desc",
    ) -> list[Passage]:
        order_dir = "DESC" if order.lower() == "desc" else "ASC"

        if tag:
            rows = self.conn.execute(
                f"SELECT id, text, tags, metadata, created_at FROM passages WHERE tags LIKE ? ORDER BY created_at {order_dir} LIMIT ?",
                (f'%"{tag}"%', limit),
            ).fetchall()
        elif search:
            rows = self.conn.execute(
                f"SELECT id, text, tags, metadata, created_at FROM passages WHERE text LIKE ? ORDER BY created_at {order_dir} LIMIT ?",
                (f"%{search}%", limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT id, text, tags, metadata, created_at FROM passages ORDER BY created_at {order_dir} LIMIT ?",
                (limit,),
            ).fetchall()

        return [self._row_to_passage(r) for r in rows]

    def search_passages(self, embedding: list[float], top_k: int = 10) -> list[Passage]:
        """Semantic vector search using sqlite-vec."""
        if not self._has_vec:
            return []
        import struct

        blob = struct.pack(f"{len(embedding)}f", *embedding)
        rows = self.conn.execute(
            """
            SELECT p.id, p.text, p.tags, p.metadata, p.created_at
            FROM passages p
            JOIN passages_vec v ON p.id = v.id
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY distance
            """,
            (blob, top_k),
        ).fetchall()
        return [self._row_to_passage(r) for r in rows]

    # --- Helpers ---

    def _store_embedding(self, passage_id: str, embedding: list[float]) -> None:
        import struct

        blob = struct.pack(f"{len(embedding)}f", *embedding)
        self.conn.execute("INSERT INTO passages_vec (id, embedding) VALUES (?, ?)", (passage_id, blob))

    def _row_to_passage(self, row: sqlite3.Row) -> Passage:
        return Passage(
            id=row["id"],
            text=row["text"],
            tags=json.loads(row["tags"]),
            metadata=json.loads(row["metadata"]),
            created_at=row["created_at"] or "",
        )
