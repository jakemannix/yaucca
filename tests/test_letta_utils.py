"""Tests for yaucca.letta_utils module."""

from unittest.mock import MagicMock

from yaucca.letta_utils import extract_archive_id


class TestExtractArchiveId:
    def test_extracts_from_passage(self) -> None:
        passage = MagicMock()
        passage.archive_id = "archive-001"
        result = extract_archive_id([passage])
        assert result == "archive-001"

    def test_returns_none_for_empty_list(self) -> None:
        result = extract_archive_id([])
        assert result is None

    def test_returns_none_when_no_attribute(self) -> None:
        obj = object()  # no archive_id attribute
        result = extract_archive_id([obj])
        assert result is None

    def test_returns_first_archive_id(self) -> None:
        p1 = MagicMock()
        p1.archive_id = "archive-first"
        p2 = MagicMock()
        p2.archive_id = "archive-second"
        result = extract_archive_id([p1, p2])
        assert result == "archive-first"
