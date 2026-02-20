"""Tests for yaucca.letta_utils module."""

from unittest.mock import MagicMock

from yaucca.letta_utils import extract_archive_id, resolve_archive_id_from_list


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


class TestResolveArchiveIdFromList:
    def test_extracts_from_list(self) -> None:
        archive = MagicMock()
        archive.id = "archive-001"
        result = resolve_archive_id_from_list([archive])
        assert result == "archive-001"

    def test_returns_none_for_empty_list(self) -> None:
        result = resolve_archive_id_from_list([])
        assert result is None

    def test_returns_first_archive_id(self) -> None:
        a1 = MagicMock()
        a1.id = "archive-first"
        a2 = MagicMock()
        a2.id = "archive-second"
        result = resolve_archive_id_from_list([a1, a2])
        assert result == "archive-first"

    def test_handles_paginated_response(self) -> None:
        """archives.list returns SyncArrayPage with .items attribute."""
        archive = MagicMock()
        archive.id = "archive-paged"
        page = MagicMock()
        page.items = [archive]
        result = resolve_archive_id_from_list(page)
        assert result == "archive-paged"

    def test_returns_none_when_no_id_attribute(self) -> None:
        obj = object()
        result = resolve_archive_id_from_list([obj])
        assert result is None
