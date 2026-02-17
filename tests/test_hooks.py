"""Tests for yaucca.hooks module."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yaucca.hooks import (
    Exchange,
    SessionState,
    _build_summary_prompt,
    _extract_all_exchanges,
    _format_transcript_for_summary,
    _load_session_state,
    _persist_exchanges,
    _save_session_state,
    _should_summarize,
    _summarize_with_claude,
    session_start,
    stop,
)


class TestSessionStart:
    def test_outputs_memory_context(self, mock_sync_letta: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("yaucca.hooks._get_letta_client", return_value=mock_sync_letta),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch.dict("os.environ", {}, clear=False),
        ):
            mock_settings.return_value.agent.agent_id = "agent-123"

            session_start({"source": "startup"})

            output = capsys.readouterr().out
            assert "<memory_blocks>" in output
            assert "<memory_metadata>" in output
            assert "<conversation_history>" in output
            assert "<archival_memory>" in output

    def test_no_agent_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("yaucca.hooks.get_settings") as mock_settings:
            mock_settings.return_value.agent.agent_id = None

            session_start({"source": "startup"})

            output = capsys.readouterr().out
            assert output == ""

    def test_letta_unreachable(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("yaucca.hooks._get_letta_client", side_effect=Exception("Connection refused")),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch.dict("os.environ", {}, clear=False),
        ):
            mock_settings.return_value.agent.agent_id = "agent-123"

            session_start({"source": "startup"})

            output = capsys.readouterr().out
            assert output == ""

    def test_skips_when_yaucca_skip_hooks_set(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch.dict("os.environ", {"YAUCCA_SKIP_HOOKS": "1"}):
            session_start({"source": "startup"})

            output = capsys.readouterr().out
            assert output == ""


class TestExtractAllExchanges:
    def test_extracts_pairs(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "human", "message": {"content": "First question"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": "First answer"}}) + "\n")
            f.write(json.dumps({"type": "human", "message": {"content": "Second question"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": "Second answer"}}) + "\n")
            path = f.name

        exchanges, total_chars, total_lines = _extract_all_exchanges(path)
        assert len(exchanges) == 2
        assert exchanges[0].user == "First question"
        assert exchanges[0].assistant == "First answer"
        assert exchanges[1].user == "Second question"
        assert exchanges[1].assistant == "Second answer"
        assert total_lines == 4
        assert total_chars > 0
        Path(path).unlink()

    def test_respects_start_line(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "human", "message": {"content": "First question"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": "First answer"}}) + "\n")
            f.write(json.dumps({"type": "human", "message": {"content": "Second question"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": "Second answer"}}) + "\n")
            path = f.name

        exchanges, total_chars, total_lines = _extract_all_exchanges(path, start_line=2)
        assert len(exchanges) == 1
        assert exchanges[0].user == "Second question"
        assert total_lines == 4
        Path(path).unlink()

    def test_handles_missing_file(self) -> None:
        exchanges, total_chars, total_lines = _extract_all_exchanges("/nonexistent/file.jsonl")
        assert exchanges == []
        assert total_chars == 0
        assert total_lines == 0

    def test_handles_list_content(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(
                json.dumps(
                    {
                        "type": "human",
                        "message": {"content": [{"type": "text", "text": "Complex input"}]},
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "Complex output"}]},
                    }
                )
                + "\n"
            )
            path = f.name

        exchanges, _, _ = _extract_all_exchanges(path)
        assert len(exchanges) == 1
        assert exchanges[0].user == "Complex input"
        assert exchanges[0].assistant == "Complex output"
        Path(path).unlink()

    def test_handles_empty_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name

        exchanges, total_chars, total_lines = _extract_all_exchanges(path)
        assert exchanges == []
        Path(path).unlink()


class TestShouldSummarize:
    def test_meets_exchange_threshold(self) -> None:
        assert _should_summarize(new_exchange_count=3, new_chars=100, min_exchanges=3, min_chars=2000) is True

    def test_meets_chars_threshold(self) -> None:
        assert _should_summarize(new_exchange_count=1, new_chars=2000, min_exchanges=3, min_chars=2000) is True

    def test_meets_both_thresholds(self) -> None:
        assert _should_summarize(new_exchange_count=5, new_chars=5000, min_exchanges=3, min_chars=2000) is True

    def test_below_both_thresholds(self) -> None:
        assert _should_summarize(new_exchange_count=1, new_chars=100, min_exchanges=3, min_chars=2000) is False


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

    def test_timeout(self) -> None:
        import subprocess as sp

        from yaucca.config import SummarizationConfig

        config = SummarizationConfig(timeout=5)

        with (
            patch("yaucca.hooks.shutil.which", return_value="/usr/local/bin/claude"),
            patch("yaucca.hooks.subprocess.run", side_effect=sp.TimeoutExpired(cmd="claude", timeout=5)),
        ):
            result = _summarize_with_claude("test prompt", config)

        assert result is None

    def test_strips_claudecode_from_env(self) -> None:
        from yaucca.config import SummarizationConfig

        config = SummarizationConfig()

        with (
            patch("yaucca.hooks.shutil.which", return_value="/usr/local/bin/claude"),
            patch("yaucca.hooks.subprocess.run") as mock_run,
            patch.dict(
                "os.environ",
                {
                    "CLAUDECODE_SESSION": "abc",
                    "CLAUDECODE_FOO": "bar",
                    "CLAUDE_CODE_ENTRYPOINT": "cli",
                    "HOME": "/home/test",
                },
            ),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="Summary", stderr="")
            _summarize_with_claude("test prompt", config)

            call_env = mock_run.call_args[1]["env"]
            assert "CLAUDECODE_SESSION" not in call_env
            assert "CLAUDECODE_FOO" not in call_env
            assert "CLAUDE_CODE_ENTRYPOINT" not in call_env
            assert call_env["YAUCCA_SKIP_HOOKS"] == "1"
            assert call_env["HOME"] == "/home/test"

    def test_nonzero_exit_code(self) -> None:
        from yaucca.config import SummarizationConfig

        config = SummarizationConfig()

        with (
            patch("yaucca.hooks.shutil.which", return_value="/usr/local/bin/claude"),
            patch("yaucca.hooks.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")
            result = _summarize_with_claude("test prompt", config)

        assert result is None

    def test_uses_model_flag(self) -> None:
        from yaucca.config import SummarizationConfig

        config = SummarizationConfig(model="haiku")

        with (
            patch("yaucca.hooks.shutil.which", return_value="/usr/local/bin/claude"),
            patch("yaucca.hooks.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="Summary", stderr="")
            _summarize_with_claude("test prompt", config)

            cmd = mock_run.call_args[0][0]
            assert "--model" in cmd
            assert "haiku" in cmd


class TestFormatTranscript:
    def test_formats_exchanges(self) -> None:
        exchanges = [
            Exchange(user="Hello", assistant="Hi there"),
            Exchange(user="Fix bug", assistant="Done"),
        ]
        result = _format_transcript_for_summary(exchanges, max_chars=10000)
        assert "--- Exchange 1 ---" in result
        assert "--- Exchange 2 ---" in result
        assert "User: Hello" in result
        assert "Assistant: Done" in result

    def test_truncates_from_start(self) -> None:
        exchanges = [
            Exchange(user="A" * 1000, assistant="B" * 1000),
            Exchange(user="Recent question", assistant="Recent answer"),
        ]
        result = _format_transcript_for_summary(exchanges, max_chars=200)
        assert "truncated" in result
        # Recent content should be preserved
        assert "Recent answer" in result


class TestBuildSummaryPrompt:
    def test_includes_metadata(self) -> None:
        exchanges = [Exchange(user="Hello", assistant="Hi")]
        prompt = _build_summary_prompt(exchanges, "myproject", "/home/user/myproject", "sess-1", 10000)
        assert "myproject" in prompt
        assert "sess-1" in prompt
        assert "Exchanges: 1" in prompt
        assert "Hello" in prompt


class TestSessionState:
    def test_load_returns_defaults_for_new_session(self, tmp_path: Path) -> None:
        with patch("yaucca.hooks.SESSIONS_DIR", tmp_path):
            state = _load_session_state("new-session")
            assert state.session_id == "new-session"
            assert state.last_persisted_line_offset == 0
            assert state.last_summary_exchange_count == 0
            assert state.last_summary_line_offset == 0
            assert state.last_summary_passage_id == ""

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
            assert loaded.session_id == "sess-1"
            assert loaded.last_persisted_line_offset == 8
            assert loaded.last_summary_exchange_count == 5
            assert loaded.last_summary_line_offset == 42
            assert loaded.last_summary_passage_id == "passage-abc"

    def test_backward_compatible_with_old_state(self, tmp_path: Path) -> None:
        """Old state files without last_persisted_line_offset still deserialize."""
        state_file = tmp_path / "old-sess.json"
        state_file.write_text(
            json.dumps(
                {
                    "session_id": "old-sess",
                    "last_summary_ts": "",
                    "last_summary_exchange_count": 3,
                    "last_summary_line_offset": 10,
                    "last_summary_passage_id": "p-1",
                }
            )
        )
        with patch("yaucca.hooks.SESSIONS_DIR", tmp_path):
            loaded = _load_session_state("old-sess")
            assert loaded.session_id == "old-sess"
            assert loaded.last_persisted_line_offset == 0  # default


class TestPersistExchanges:
    def test_persists_with_archive_id(self, mock_sync_letta: MagicMock) -> None:
        exchanges = [
            Exchange(user="Hello", assistant="Hi there"),
            Exchange(user="Fix bug", assistant="Done"),
        ]

        _persist_exchanges(mock_sync_letta, "agent-123", "archive-001", exchanges, "sess-1", "myproject")

        assert mock_sync_letta.archives.passages.create.call_count == 2

        # Check first call
        call_args = mock_sync_letta.archives.passages.create.call_args_list[0]
        assert call_args[0][0] == "archive-001"
        assert "User: Hello" in call_args[1]["text"]
        assert call_args[1]["tags"] == ["exchange"]
        assert call_args[1]["metadata"]["session_id"] == "sess-1"
        assert call_args[1]["metadata"]["project"] == "myproject"

    def test_persists_without_archive_id(self, mock_sync_letta: MagicMock) -> None:
        exchanges = [Exchange(user="Hello", assistant="Hi")]

        _persist_exchanges(mock_sync_letta, "agent-123", None, exchanges, "sess-1", "myproject")

        mock_sync_letta.agents.passages.create.assert_called_once()
        call_args = mock_sync_letta.agents.passages.create.call_args
        assert call_args[0][0] == "agent-123"
        assert call_args[1]["tags"] == ["exchange"]


class TestStop:
    def _make_transcript(self, exchanges: int = 4) -> str:
        """Create a temporary transcript file with the given number of exchanges."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for i in range(exchanges):
                f.write(json.dumps({"type": "human", "message": {"content": f"Question {i + 1}"}}) + "\n")
                f.write(json.dumps({"type": "assistant", "message": {"content": f"Answer {i + 1}"}}) + "\n")
            return f.name

    def test_persists_raw_exchanges_with_tags(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """Layer 1: exchanges are persisted with correct tags/metadata."""
        transcript_path = self._make_transcript(exchanges=2)

        with (
            patch("yaucca.hooks._get_letta_client", return_value=mock_sync_letta),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch("yaucca.hooks.SESSIONS_DIR", tmp_path),
            patch.dict("os.environ", {}, clear=False),
        ):
            from yaucca.config import SummarizationConfig

            mock_settings.return_value.agent.agent_id = "agent-123"
            mock_settings.return_value.summary = SummarizationConfig(min_exchanges=10, min_chars=100000)

            stop(
                {
                    "session_id": "sess-1",
                    "transcript_path": transcript_path,
                    "cwd": "/home/user/project",
                    "stop_hook_active": False,
                }
            )

            # 2 exchanges should have been persisted
            assert mock_sync_letta.archives.passages.create.call_count == 2

            # Check tags on first call
            call_args = mock_sync_letta.archives.passages.create.call_args_list[0]
            assert call_args[1]["tags"] == ["exchange"]
            assert call_args[1]["metadata"]["session_id"] == "sess-1"

        Path(transcript_path).unlink()

    def test_persists_exchanges_below_summary_threshold(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """Layer 1 works even without Layer 2 triggering."""
        transcript_path = self._make_transcript(exchanges=1)

        with (
            patch("yaucca.hooks._get_letta_client", return_value=mock_sync_letta),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch("yaucca.hooks.SESSIONS_DIR", tmp_path),
            patch.dict("os.environ", {}, clear=False),
        ):
            from yaucca.config import SummarizationConfig

            mock_settings.return_value.agent.agent_id = "agent-123"
            mock_settings.return_value.summary = SummarizationConfig(min_exchanges=3, min_chars=2000)

            stop(
                {
                    "session_id": "sess-1",
                    "transcript_path": transcript_path,
                    "cwd": "/home/user/project",
                    "stop_hook_active": False,
                }
            )

            # Exchange should still be persisted (Layer 1)
            assert mock_sync_letta.archives.passages.create.call_count == 1
            call_args = mock_sync_letta.archives.passages.create.call_args
            persisted_text = call_args[1]["text"] if "text" in call_args[1] else call_args[0][1]
            assert "Question 1" in persisted_text
            assert call_args[1]["tags"] == ["exchange"]

        Path(transcript_path).unlink()

    def test_summarizes_above_threshold(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """Both Layer 1 (exchanges) and Layer 2 (summary) fire above threshold."""
        transcript_path = self._make_transcript(exchanges=4)

        with (
            patch("yaucca.hooks._get_letta_client", return_value=mock_sync_letta),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch("yaucca.hooks._summarize_with_claude", return_value="LLM summary of session"),
            patch("yaucca.hooks.SESSIONS_DIR", tmp_path),
            patch.dict("os.environ", {}, clear=False),
        ):
            from yaucca.config import SummarizationConfig

            mock_settings.return_value.agent.agent_id = "agent-123"
            mock_settings.return_value.summary = SummarizationConfig(min_exchanges=3, min_chars=100)

            stop(
                {
                    "session_id": "sess-1",
                    "transcript_path": transcript_path,
                    "cwd": "/home/user/project",
                    "stop_hook_active": False,
                }
            )

            # 4 exchange passages + 1 summary passage = 5 total
            assert mock_sync_letta.archives.passages.create.call_count == 5

            # Last call should be the summary with tags
            summary_call = mock_sync_letta.archives.passages.create.call_args_list[-1]
            summary_text = summary_call[1]["text"] if "text" in summary_call[1] else summary_call[0][1]
            assert "LLM summary of session" in summary_text
            assert summary_call[1]["tags"] == ["summary"]

        Path(transcript_path).unlink()

    def test_summarization_failure_logs_error(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """When summarization fails, exchanges are already persisted, error is logged."""
        transcript_path = self._make_transcript(exchanges=4)

        with (
            patch("yaucca.hooks._get_letta_client", return_value=mock_sync_letta),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch("yaucca.hooks._summarize_with_claude", return_value=None),
            patch("yaucca.hooks.SESSIONS_DIR", tmp_path),
            patch("yaucca.hooks.logger") as mock_logger,
            patch.dict("os.environ", {}, clear=False),
        ):
            from yaucca.config import SummarizationConfig

            mock_settings.return_value.agent.agent_id = "agent-123"
            mock_settings.return_value.summary = SummarizationConfig(min_exchanges=3, min_chars=100)

            stop(
                {
                    "session_id": "sess-1",
                    "transcript_path": transcript_path,
                    "cwd": "/home/user/project",
                    "stop_hook_active": False,
                }
            )

            # Exchanges should still be persisted (4 calls)
            assert mock_sync_letta.archives.passages.create.call_count == 4

            # Error should be logged
            mock_logger.error.assert_any_call("Summarization failed — raw exchanges already persisted")

        Path(transcript_path).unlink()

    def test_letta_failure_logs_error(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """Connection failure logs error and returns early."""
        transcript_path = self._make_transcript(exchanges=2)

        with (
            patch("yaucca.hooks._get_letta_client", side_effect=Exception("Connection refused")),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch("yaucca.hooks.SESSIONS_DIR", tmp_path),
            patch("yaucca.hooks.logger") as mock_logger,
            patch.dict("os.environ", {}, clear=False),
        ):
            from yaucca.config import SummarizationConfig

            mock_settings.return_value.agent.agent_id = "agent-123"
            mock_settings.return_value.summary = SummarizationConfig()

            stop(
                {
                    "session_id": "sess-1",
                    "transcript_path": transcript_path,
                    "cwd": "/home/user/project",
                    "stop_hook_active": False,
                }
            )

            mock_logger.error.assert_called_once()
            assert "Connection refused" in str(mock_logger.error.call_args)

        Path(transcript_path).unlink()

    def test_skips_when_hook_active(self, mock_sync_letta: MagicMock) -> None:
        with patch("yaucca.hooks.get_settings") as mock_settings:
            mock_settings.return_value.agent.agent_id = "agent-123"

            stop({"stop_hook_active": True})

            mock_sync_letta.archives.passages.create.assert_not_called()

    def test_skips_no_agent_id(self) -> None:
        with patch("yaucca.hooks.get_settings") as mock_settings:
            mock_settings.return_value.agent.agent_id = None
            stop({"stop_hook_active": False})

    def test_skips_no_transcript(self, mock_sync_letta: MagicMock) -> None:
        with (
            patch("yaucca.hooks._get_letta_client", return_value=mock_sync_letta),
            patch("yaucca.hooks.get_settings") as mock_settings,
            patch.dict("os.environ", {}, clear=False),
        ):
            mock_settings.return_value.agent.agent_id = "agent-123"

            stop({"stop_hook_active": False, "transcript_path": ""})

            mock_sync_letta.archives.passages.create.assert_not_called()

    def test_skips_when_yaucca_skip_hooks_set(self, mock_sync_letta: MagicMock) -> None:
        with patch.dict("os.environ", {"YAUCCA_SKIP_HOOKS": "1"}):
            stop({"stop_hook_active": False, "transcript_path": "/some/path"})

            mock_sync_letta.archives.passages.create.assert_not_called()
