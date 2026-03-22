"""Tests for yaucca.cloud.db — SQLite storage layer."""

import pytest

from yaucca.cloud.db import Database


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
