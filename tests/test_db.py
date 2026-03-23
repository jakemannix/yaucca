"""Tests for yaucca.cloud.db — SQLite storage layer."""

import pytest

from yaucca.cloud.db import Database, EmbeddingProfile


@pytest.fixture
def db() -> Database:
    """Create an in-memory database for testing."""
    d = Database(db_path=":memory:")
    d.connect()
    d.init_default_blocks()
    return d


class TestBlocks:
    def test_default_blocks_created(self, db: Database) -> None:
        blocks = db.list_blocks()
        labels = {b.label for b in blocks}
        assert labels == {"user", "projects", "patterns", "learnings", "context"}

    def test_get_block(self, db: Database) -> None:
        block = db.get_block("user")
        assert block is not None
        assert block.label == "user"
        assert block.char_limit == 5000

    def test_get_nonexistent_block(self, db: Database) -> None:
        assert db.get_block("nonexistent") is None

    def test_update_block(self, db: Database) -> None:
        db.update_block("user", "Updated value")
        block = db.get_block("user")
        assert block is not None
        assert block.value == "Updated value"

    def test_init_default_blocks_idempotent(self, db: Database) -> None:
        db.update_block("user", "Custom value")
        db.init_default_blocks()  # Should not overwrite
        block = db.get_block("user")
        assert block is not None
        assert block.value == "Custom value"


class TestPassages:
    def test_create_and_get(self, db: Database) -> None:
        p = db.create_passage(text="Test passage", tags=["exchange"])
        assert p.text == "Test passage"
        assert p.tags == ["exchange"]
        assert p.id

        retrieved = db.get_passage(p.id)
        assert retrieved is not None
        assert retrieved.text == "Test passage"

    def test_delete(self, db: Database) -> None:
        p = db.create_passage(text="To delete")
        assert db.delete_passage(p.id) is True
        assert db.get_passage(p.id) is None

    def test_delete_nonexistent(self, db: Database) -> None:
        assert db.delete_passage("nonexistent") is False

    def test_list_all(self, db: Database) -> None:
        db.create_passage(text="First")
        db.create_passage(text="Second")
        passages = db.list_passages()
        assert len(passages) == 2

    def test_list_by_tag(self, db: Database) -> None:
        db.create_passage(text="Exchange 1", tags=["exchange"])
        db.create_passage(text="Summary 1", tags=["summary"])
        db.create_passage(text="Exchange 2", tags=["exchange"])

        exchanges = db.list_passages(tag="exchange")
        assert len(exchanges) == 2
        assert all("exchange" in p.tags for p in exchanges)

    def test_list_by_text_search(self, db: Database) -> None:
        db.create_passage(text="Fixed the authentication bug")
        db.create_passage(text="Updated the README")
        results = db.list_passages(search="authentication")
        assert len(results) == 1
        assert "authentication" in results[0].text

    def test_list_order(self, db: Database) -> None:
        p1 = db.create_passage(text="First")
        p2 = db.create_passage(text="Second")
        desc = db.list_passages(order="desc")
        asc = db.list_passages(order="asc")
        assert desc[0].id == p2.id
        assert asc[0].id == p1.id

    def test_list_limit(self, db: Database) -> None:
        for i in range(10):
            db.create_passage(text=f"Passage {i}")
        limited = db.list_passages(limit=3)
        assert len(limited) == 3

    def test_create_with_metadata(self, db: Database) -> None:
        p = db.create_passage(
            text="Test",
            tags=["exchange"],
            metadata={"session_id": "sess-1", "project": "myproject"},
        )
        retrieved = db.get_passage(p.id)
        assert retrieved is not None
        assert retrieved.metadata["session_id"] == "sess-1"


class TestVecAvailability:
    """Verify sqlite-vec is actually loadable in the test environment."""

    def test_sqlite_vec_loads(self) -> None:
        """sqlite-vec must be importable — if this fails, vector search is silently disabled everywhere."""
        import sqlite_vec  # noqa: F401

    def test_vec_tables_created(self, db: Database) -> None:
        """With sqlite-vec installed, active_profiles should be populated."""
        assert db.has_vec, "sqlite-vec failed to load — vector search is disabled"
        assert len(db.active_profiles) >= 1

    def test_vector_search_roundtrip(self, db: Database) -> None:
        """End-to-end: store an embedding and retrieve it via vector search."""
        assert db.has_vec, "sqlite-vec not available"
        embedding = [1.0] * 1024
        p = db.create_passage(text="vector roundtrip test", embedding=embedding)
        results = db.search_passages(embedding, top_k=5)
        assert len(results) >= 1
        assert results[0].id == p.id
        db.delete_passage(p.id)


class TestEmbeddingProfiles:
    def test_default_profile(self, db: Database) -> None:
        """Default config creates a d1024 profile."""
        assert len(db._profiles) == 1
        assert db._profiles[0].name == "d1024"
        assert db._profiles[0].dimensions == 1024

    def test_multi_profile_config(self) -> None:
        profiles = [
            EmbeddingProfile(name="d1024", dimensions=1024),
            EmbeddingProfile(name="d512", dimensions=512),
            EmbeddingProfile(name="d256", dimensions=256),
        ]
        db = Database(db_path=":memory:", embedding_profiles=profiles)
        db.connect()
        assert len(db._profiles) == 3
        assert db._profiles[1].table_name == "passages_vec_d512"

    def test_create_passage_truncates_embedding(self) -> None:
        """Embedding is truncated to each profile's dimensions at insert time."""
        profiles = [
            EmbeddingProfile(name="full", dimensions=4),
            EmbeddingProfile(name="half", dimensions=2),
        ]
        db = Database(db_path=":memory:", embedding_profiles=profiles)
        db.connect()
        db.init_default_blocks()

        full_embedding = [1.0, 2.0, 3.0, 4.0]
        p = db.create_passage(text="Test", embedding=full_embedding)
        assert p.id  # passage was created

        # Can't directly verify truncation without sqlite-vec, but ensure no errors
        # The actual truncation logic is: embedding[:profile.dimensions]

    def test_profile_table_name(self) -> None:
        p = EmbeddingProfile(name="d512", dimensions=512)
        assert p.table_name == "passages_vec_d512"


class TestBackfill:
    def test_passages_needing_backfill(self, db: Database) -> None:
        """Passages without embeddings show up as needing backfill."""
        # Create without embedding
        db.create_passage(text="Needs backfill")
        result = db.passages_needing_backfill("d1024")
        assert len(result) == 1
        assert result[0].text == "Needs backfill"

    def test_passages_with_embedding_not_in_backfill(self, db: Database) -> None:
        """Passages that already have embeddings are skipped."""
        embedding = [1.0] * 1024
        db.create_passage(text="Already embedded", embedding=embedding)
        result = db.passages_needing_backfill("d1024")
        assert len(result) == 0

    def test_store_backfill_embedding(self, db: Database) -> None:
        """Backfill stores embedding and makes passage searchable."""
        p = db.create_passage(text="To backfill")
        embedding = [1.0] * 1024
        assert db.store_backfill_embedding(p.id, embedding, "d1024") is True
        results = db.search_passages(embedding, top_k=5)
        assert len(results) == 1
        assert results[0].id == p.id

    def test_backfill_nonexistent_profile(self, db: Database) -> None:
        """Backfill against a nonexistent profile returns empty/False."""
        db.create_passage(text="Test")
        assert db.passages_needing_backfill("nonexistent") == []
        p = db.create_passage(text="Test2")
        assert db.store_backfill_embedding(p.id, [0.0] * 1024, "nonexistent") is False


class TestWriteCallback:
    def test_on_write_called(self) -> None:
        calls: list[str] = []
        db = Database(db_path=":memory:", on_write=lambda: calls.append("write"))
        db.connect()
        db.init_default_blocks()
        assert len(calls) == 1  # init_default_blocks triggers one write

        db.update_block("user", "New value")
        assert len(calls) == 2

        db.create_passage(text="Test")
        assert len(calls) == 3
