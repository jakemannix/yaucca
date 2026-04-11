"""Tests for yaucca.prompt module."""

from tests.conftest import (
    make_coding_block_set,
    make_exchange_passage,
    make_summary_passage,
)
from yaucca.prompt import (
    render_archival_summaries,
    render_conversation_history,
    render_full_context,
    render_memory_blocks,
    render_memory_metadata,
    render_tagged_section,
)


class TestRenderMemoryBlocks:
    def test_renders_all_blocks_in_order(self) -> None:
        blocks = make_coding_block_set()
        result = render_memory_blocks(blocks)

        assert "<memory_blocks>" in result
        assert "</memory_blocks>" in result

        # Verify BLOCK_ORDER is respected
        user_pos = result.index("<user>")
        projects_pos = result.index("<projects>")
        patterns_pos = result.index("<patterns>")
        learnings_pos = result.index("<learnings>")
        context_pos = result.index("<context>")

        assert user_pos < projects_pos < patterns_pos < learnings_pos < context_pos

    def test_includes_metadata(self) -> None:
        blocks = make_coding_block_set()
        result = render_memory_blocks(blocks)

        assert "chars_current=" in result
        assert "chars_limit=" in result
        assert "<description>" in result

    def test_includes_values(self) -> None:
        blocks = make_coding_block_set()
        result = render_memory_blocks(blocks)

        assert "Jake Mannix" in result
        assert "Nameless agent" in result

    def test_extra_blocks_appended(self) -> None:
        from tests.conftest import make_block

        blocks = make_coding_block_set()
        blocks.append(make_block(label="custom", value="Custom block"))
        result = render_memory_blocks(blocks)

        assert "<custom>" in result
        assert "</custom>" in result
        # Custom should come after the ordered blocks
        context_pos = result.index("</context>")
        custom_pos = result.index("<custom>")
        assert custom_pos > context_pos


class TestRenderMemoryMetadata:
    def test_renders_metadata(self) -> None:
        result = render_memory_metadata(archival_count=42, exchange_count=15)

        assert "<memory_metadata>" in result
        assert "</memory_metadata>" in result
        assert "42 total memories" in result
        assert "15 previous exchanges" in result
        assert "current time" in result


class TestRenderConversationHistory:
    def test_with_exchanges(self) -> None:
        exchanges = [
            make_exchange_passage(passage_id="e1", user="Fix the bug", assistant="Done!"),
            make_exchange_passage(passage_id="e2", user="Add tests", assistant="Added 3 tests."),
        ]

        result = render_conversation_history(exchanges)

        assert "<conversation_history>" in result
        assert "</conversation_history>" in result
        assert "Fix the bug" in result
        assert "Done!" in result
        assert "Add tests" in result

    def test_empty_history(self) -> None:
        result = render_conversation_history([])
        assert "No previous conversation exchanges found" in result

    def test_renders_chronologically(self) -> None:
        """Passages come in descending order; render should reverse to chronological."""
        import datetime

        exchanges = [
            make_exchange_passage(
                passage_id="e2",
                user="Second",
                assistant="Reply 2",
                created_at=datetime.datetime(2024, 1, 15, 11, 0, 0),
            ),
            make_exchange_passage(
                passage_id="e1",
                user="First",
                assistant="Reply 1",
                created_at=datetime.datetime(2024, 1, 15, 10, 0, 0),
            ),
        ]

        result = render_conversation_history(exchanges)
        first_pos = result.index("First")
        second_pos = result.index("Second")
        assert first_pos < second_pos


class TestRenderArchivalSummaries:
    def test_with_summaries(self) -> None:
        summaries = [
            make_summary_passage(text="Session summary: fixed config"),
        ]

        result = render_archival_summaries(summaries)

        assert "<archival_memory>" in result
        assert "</archival_memory>" in result
        assert "Session summary: fixed config" in result

    def test_empty_summaries(self) -> None:
        result = render_archival_summaries([])
        assert "No archival memories found" in result


class TestRenderTaggedSection:
    def test_renders_items(self) -> None:
        passages = [
            make_exchange_passage(passage_id="p1", user="Buy groceries", assistant=""),
            make_exchange_passage(passage_id="p2", user="Fix bike", assistant=""),
        ]
        result = render_tagged_section("@next", passages)
        assert '<tagged_items tag="@next">' in result
        assert "</tagged_items>" in result
        assert "Buy groceries" in result
        assert "Fix bike" in result

    def test_empty_passages(self) -> None:
        result = render_tagged_section("@inbox", [])
        assert result == ""

    def test_shows_due_date(self) -> None:
        from tests.conftest import MockPassage

        passages = [MockPassage(text="Water heater", tags=["@next", "due:2026-04-12"])]
        result = render_tagged_section("@next", passages)
        assert "due:2026-04-12" in result


class TestRenderFullContext:
    def test_combines_all_sections(self) -> None:
        blocks = make_coding_block_set()
        exchanges = [make_exchange_passage()]
        summaries = [make_summary_passage()]

        result = render_full_context(
            blocks=blocks,
            exchanges=exchanges,
            summaries=summaries,
            archival_count=5,
            exchange_count=1,
        )

        assert "<memory_blocks>" in result
        assert "<memory_metadata>" in result
        assert "<conversation_history>" in result
        assert "<archival_memory>" in result

    def test_with_tagged_sections(self) -> None:
        from tests.conftest import MockPassage

        blocks = make_coding_block_set()
        exchanges = [make_exchange_passage()]
        summaries = [make_summary_passage()]
        tagged = {"@next": [MockPassage(text="Buy groceries", tags=["@next"])]}

        result = render_full_context(
            blocks=blocks,
            exchanges=exchanges,
            summaries=summaries,
            archival_count=5,
            exchange_count=1,
            tagged_sections=tagged,
        )

        assert '<tagged_items tag="@next">' in result
        assert "Buy groceries" in result
