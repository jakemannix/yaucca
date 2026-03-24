"""Tests for yaucca.hooks module — cloud API version."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yaucca.hooks import (
    SessionState,
    Turn,
    _build_summary_prompt,
    _extract_turns,
    _format_transcript_for_summary,
    _load_session_state,
    _save_session_state,
    _should_summarize,
    _summarize_with_claude,
    session_start,
    stop,
)


def _mock_cloud_settings(url: str = "http://localhost:8283", auth_token: str | None = None, required: bool = False):  # type: ignore[no-untyped-def]
    """Create mock settings for cloud API."""
    mock = MagicMock()
    mock.cloud.url = url
    mock.cloud.auth_token = auth_token
    mock.cloud.required = required
    mock.summary = MagicMock()
    mock.summary.enabled = True
    mock.summary.min_exchanges = 3
    mock.summary.min_chars = 2000
    mock.summary.timeout = 90
    mock.summary.max_transcript_chars = 100_000
    mock.summary.claude_command = "claude"
    mock.summary.model = ""
    return mock


class TestSessionStart:
    def test_outputs_memory_context(self, capsys: pytest.CaptureFixture[str]) -> None:
        mock_client = MagicMock()
        # Mock blocks response
        blocks_resp = MagicMock()
        blocks_resp.json.return_value = [
            {"label": "user", "value": "Jake", "description": "User info", "limit": 5000},
            {"label": "context", "value": "Working", "description": "Context", "limit": 5000},
        ]
        blocks_resp.raise_for_status = MagicMock()

        # Mock passages response
        passages_resp = MagicMock()
        passages_resp.json.return_value = [
            {"id": "p1", "text": "User: Hi\nAssistant: Hello", "tags": ["exchange"], "metadata": {}, "created_at": "2024-01-15"},
            {"id": "p2", "text": "Session summary", "tags": ["summary"], "metadata": {}, "created_at": "2024-01-15"},
        ]
        passages_resp.raise_for_status = MagicMock()

        mock_client.get.side_effect = lambda path, **kw: blocks_resp if path == "/api/blocks" else passages_resp

        with (
            patch("yaucca.hooks._cloud_client", return_value=(mock_client, "http://localhost:8283")),
            patch.dict("os.environ", {}, clear=False),
        ):
            session_start({"source": "startup"})

            output = capsys.readouterr().out
            assert "<memory_blocks>" in output
            assert "<memory_metadata>" in output
            assert "<conversation_history>" in output

    def test_cloud_unreachable_optional(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When YAUCCA_REQUIRED is false (default), cloud failure degrades silently."""
        with (
            patch("yaucca.hooks._cloud_client", side_effect=Exception("Connection refused")),
            patch("yaucca.hooks.get_settings", return_value=_mock_cloud_settings(required=False)),
            patch.dict("os.environ", {}, clear=False),
        ):
            session_start({"source": "startup"})
            output = capsys.readouterr().out
            assert output == ""

    def test_cloud_unreachable_required(self) -> None:
        """When YAUCCA_REQUIRED is true, cloud failure exits non-zero."""
        with (
            patch("yaucca.hooks._cloud_client", side_effect=Exception("Connection refused")),
            patch("yaucca.hooks.get_settings", return_value=_mock_cloud_settings(required=True)),
            patch.dict("os.environ", {}, clear=False),
            pytest.raises(SystemExit) as exc_info,
        ):
            session_start({"source": "startup"})
        assert exc_info.value.code == 1

    def test_skips_when_yaucca_skip_hooks_set(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch.dict("os.environ", {"YAUCCA_SKIP_HOOKS": "1"}):
            session_start({"source": "startup"})
            output = capsys.readouterr().out
            assert output == ""


class TestExtractTurns:
    def test_extracts_simple_turns(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "First question"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "First answer"}]}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"content": "Second question"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Second answer"}]}}) + "\n")
            path = f.name

        turns, total_chars, total_lines = _extract_turns(path)
        assert len(turns) == 2
        assert "User: First question" in turns[0].format()
        assert "Assistant: First answer" in turns[0].format()
        assert total_lines == 4
        Path(path).unlink()

    def test_respects_start_line(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "First question"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "First answer"}]}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"content": "Second question"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Second answer"}]}}) + "\n")
            path = f.name

        turns, total_chars, total_lines = _extract_turns(path, start_line=2)
        assert len(turns) == 1
        assert "User: Second question" in turns[0].format()
        Path(path).unlink()

    def test_handles_missing_file(self) -> None:
        turns, total_chars, total_lines = _extract_turns("/nonexistent/file.jsonl")
        assert turns == []

    def test_handles_thinking_content(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "Explain"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "Let me analyze"},
                {"type": "text", "text": "Here is my analysis"},
            ]}}) + "\n")
            path = f.name

        turns, _, _ = _extract_turns(path)
        formatted = turns[0].format()
        assert "Thinking: Let me analyze" in formatted
        assert "Assistant: Here is my analysis" in formatted
        Path(path).unlink()

    def test_handles_tool_use_and_result(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "Run tests"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_123", "name": "Bash", "input": {"command": "pytest"}},
            ]}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_123", "content": "5 passed"},
            ]}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "All pass!"}]}}) + "\n")
            path = f.name

        turns, _, _ = _extract_turns(path)
        assert len(turns) == 1
        formatted = turns[0].format()
        assert "Tool: Bash(" in formatted
        assert "Tool Result (toolu_123): 5 passed" in formatted
        assert "Assistant: All pass!" in formatted
        Path(path).unlink()


class TestShouldSummarize:
    def test_meets_exchange_threshold(self) -> None:
        assert _should_summarize(3, 100, 3, 2000) is True

    def test_meets_chars_threshold(self) -> None:
        assert _should_summarize(1, 2000, 3, 2000) is True

    def test_below_both_thresholds(self) -> None:
        assert _should_summarize(1, 100, 3, 2000) is False


class TestSummarizeWithClaude:
    def test_success(self) -> None:
        from yaucca.config import SummarizationConfig

        config = SummarizationConfig()
        with (
            patch("yaucca.hooks.shutil.which", return_value="/usr/local/bin/claude"),
            patch("yaucca.hooks.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="Session summary here.", stderr="")
            result = _summarize_with_claude("test prompt", config)
        assert result == "Session summary here."

    def test_not_found(self) -> None:
        from yaucca.config import SummarizationConfig

        config = SummarizationConfig()
        with patch("yaucca.hooks.shutil.which", return_value=None):
            result = _summarize_with_claude("test prompt", config)
        assert result is None

    def test_strips_claudecode_from_env(self) -> None:
        from yaucca.config import SummarizationConfig

        config = SummarizationConfig()
        with (
            patch("yaucca.hooks.shutil.which", return_value="/usr/local/bin/claude"),
            patch("yaucca.hooks.subprocess.run") as mock_run,
            patch.dict("os.environ", {"CLAUDECODE_SESSION": "abc", "CLAUDE_CODE_ENTRYPOINT": "cli", "HOME": "/home/test"}),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="Summary", stderr="")
            _summarize_with_claude("test prompt", config)

            call_env = mock_run.call_args[1]["env"]
            assert "CLAUDECODE_SESSION" not in call_env
            assert "CLAUDE_CODE_ENTRYPOINT" not in call_env
            assert call_env["YAUCCA_SKIP_HOOKS"] == "1"


class TestFormatTranscript:
    def test_formats_turns(self) -> None:
        turns = [
            Turn(entries=["User: Hello", "Assistant: Hi there"]),
            Turn(entries=["User: Fix bug", "Assistant: Done"]),
        ]
        result = _format_transcript_for_summary(turns, max_chars=10000)
        assert "--- Turn 1 ---" in result
        assert "--- Turn 2 ---" in result

    def test_truncates_from_start(self) -> None:
        turns = [
            Turn(entries=["User: " + "A" * 1000, "Assistant: " + "B" * 1000]),
            Turn(entries=["User: Recent question", "Assistant: Recent answer"]),
        ]
        result = _format_transcript_for_summary(turns, max_chars=200)
        assert "truncated" in result
        assert "Recent answer" in result


class TestBuildSummaryPrompt:
    def test_includes_metadata(self) -> None:
        turns = [Turn(entries=["User: Hello", "Assistant: Hi"])]
        prompt = _build_summary_prompt(turns, "myproject", "/home/user/myproject", "sess-1", 10000)
        assert "myproject" in prompt
        assert "sess-1" in prompt


class TestSessionState:
    def test_load_returns_defaults_for_new_session(self, tmp_path: Path) -> None:
        with patch("yaucca.hooks.SESSIONS_DIR", tmp_path):
            state = _load_session_state("new-session")
            assert state.session_id == "new-session"
            assert state.last_persisted_line_offset == 0

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        with patch("yaucca.hooks.SESSIONS_DIR", tmp_path):
            state = SessionState(
                session_id="sess-1",
                last_persisted_line_offset=8,
                last_summary_ts="2024-01-15T10:30:00+00:00",
                last_summary_exchange_count=5,
                last_summary_line_offset=42,
                last_summary_passage_id="passage-abc",
            )
            _save_session_state(state)

            loaded = _load_session_state("sess-1")
            assert loaded.last_persisted_line_offset == 8
            assert loaded.last_summary_passage_id == "passage-abc"


def _make_transcript(turns: int = 4) -> str:
    """Create a temporary transcript file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for i in range(turns):
            f.write(json.dumps({"type": "user", "message": {"content": f"Question {i + 1}"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": f"Answer {i + 1}"}]}}) + "\n")
        return f.name


class TestStop:
    def _mock_client_for_stop(self) -> MagicMock:
        """Create a mock httpx.Client for stop hook tests."""
        client = MagicMock()
        # Health check succeeds
        health_resp = MagicMock()
        health_resp.raise_for_status = MagicMock()
        client.get.return_value = health_resp
        # Passage creation succeeds
        post_resp = MagicMock()
        post_resp.raise_for_status = MagicMock()
        post_resp.json.return_value = {"id": "new-passage-id"}
        post_resp.status_code = 201
        client.post.return_value = post_resp
        # Block update succeeds
        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()
        client.put.return_value = put_resp
        return client

    def test_persists_raw_turns(self, tmp_path: Path) -> None:
        transcript_path = _make_transcript(turns=2)
        mock_client = self._mock_client_for_stop()

        from yaucca.config import SummarizationConfig

        with (
            patch("yaucca.hooks._cloud_client", return_value=(mock_client, "http://localhost:8283")),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch("yaucca.hooks._summarize_with_claude", return_value=None),
            patch("yaucca.hooks.SESSIONS_DIR", tmp_path),
            patch.dict("os.environ", {}, clear=False),
        ):
            mock_settings.return_value = _mock_cloud_settings()
            mock_settings.return_value.summary = SummarizationConfig(min_exchanges=10, min_chars=100000)

            stop({
                "session_id": "sess-1",
                "transcript_path": transcript_path,
                "cwd": "/home/user/project",
                "stop_hook_active": False,
            })

            # 2 turns should be posted
            assert mock_client.post.call_count == 2
            # Check first call posts to /api/passages with exchange tag
            call_kwargs = mock_client.post.call_args_list[0][1]
            assert call_kwargs["json"]["tags"] == ["exchange"]

        Path(transcript_path).unlink()

    def test_skips_when_hook_active(self) -> None:
        stop({"stop_hook_active": True})

    def test_skips_when_yaucca_skip_hooks_set(self) -> None:
        with patch.dict("os.environ", {"YAUCCA_SKIP_HOOKS": "1"}):
            stop({"stop_hook_active": False, "transcript_path": "/some/path"})

    def test_skips_no_transcript(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=False),
        ):
            stop({"stop_hook_active": False, "transcript_path": ""})

    def test_cloud_failure_logs_error_optional(self, tmp_path: Path) -> None:
        """When YAUCCA_REQUIRED is false, cloud failure logs error and returns."""
        transcript_path = _make_transcript(turns=2)
        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("Connection refused")

        with (
            patch("yaucca.hooks._cloud_client", return_value=(mock_client, "http://localhost:8283")),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch("yaucca.hooks.SESSIONS_DIR", tmp_path),
            patch("yaucca.hooks.logger") as mock_logger,
            patch.dict("os.environ", {}, clear=False),
        ):
            mock_settings.return_value = _mock_cloud_settings(required=False)

            stop({
                "session_id": "sess-1",
                "transcript_path": transcript_path,
                "cwd": "/home/user/project",
                "stop_hook_active": False,
            })

            mock_logger.error.assert_called_once()

        Path(transcript_path).unlink()

    def test_cloud_failure_exits_when_required(self, tmp_path: Path) -> None:
        """When YAUCCA_REQUIRED is true, cloud failure exits non-zero."""
        transcript_path = _make_transcript(turns=2)
        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("Connection refused")

        with (
            patch("yaucca.hooks._cloud_client", return_value=(mock_client, "http://localhost:8283")),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch("yaucca.hooks.SESSIONS_DIR", tmp_path),
            patch.dict("os.environ", {}, clear=False),
            pytest.raises(SystemExit) as exc_info,
        ):
            mock_settings.return_value = _mock_cloud_settings(required=True)

            stop({
                "session_id": "sess-1",
                "transcript_path": transcript_path,
                "cwd": "/home/user/project",
                "stop_hook_active": False,
            })

        assert exc_info.value.code == 1
        Path(transcript_path).unlink()
