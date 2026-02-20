"""Tests for yaucca.hooks module."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yaucca.hooks import (
    Turn,
    SessionState,
    _build_summary_prompt,
    _extract_turns,
    _format_transcript_for_summary,
    _load_session_state,
    _persist_turns,
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


class TestExtractTurns:
    def test_extracts_simple_turns(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "First question"}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "First answer"}]},
                    }
                )
                + "\n"
            )
            f.write(json.dumps({"type": "user", "message": {"content": "Second question"}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "Second answer"}]},
                    }
                )
                + "\n"
            )
            path = f.name

        turns, total_chars, total_lines = _extract_turns(path)
        assert len(turns) == 2
        assert "User: First question" in turns[0].format()
        assert "Assistant: First answer" in turns[0].format()
        assert "User: Second question" in turns[1].format()
        assert "Assistant: Second answer" in turns[1].format()
        assert total_lines == 4
        assert total_chars > 0
        Path(path).unlink()

    def test_respects_start_line(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "First question"}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "First answer"}]},
                    }
                )
                + "\n"
            )
            f.write(json.dumps({"type": "user", "message": {"content": "Second question"}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "Second answer"}]},
                    }
                )
                + "\n"
            )
            path = f.name

        turns, total_chars, total_lines = _extract_turns(path, start_line=2)
        assert len(turns) == 1
        assert "User: Second question" in turns[0].format()
        assert total_lines == 4
        Path(path).unlink()

    def test_handles_missing_file(self) -> None:
        turns, total_chars, total_lines = _extract_turns("/nonexistent/file.jsonl")
        assert turns == []
        assert total_chars == 0
        assert total_lines == 0

    def test_handles_thinking_content(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "Explain this code"}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "thinking", "thinking": "Let me analyze the code structure carefully"},
                                {"type": "text", "text": "Here is my analysis"},
                            ]
                        },
                    }
                )
                + "\n"
            )
            path = f.name

        turns, _, _ = _extract_turns(path)
        assert len(turns) == 1
        formatted = turns[0].format()
        assert "User: Explain this code" in formatted
        assert "Thinking: Let me analyze the code structure carefully" in formatted
        assert "Assistant: Here is my analysis" in formatted
        Path(path).unlink()

    def test_handles_tool_use_content(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "Run tests"}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_123",
                                    "name": "Bash",
                                    "input": {"command": "pytest", "description": "Run tests"},
                                }
                            ]
                        },
                    }
                )
                + "\n"
            )
            path = f.name

        turns, _, _ = _extract_turns(path)
        assert len(turns) == 1
        formatted = turns[0].format()
        assert "User: Run tests" in formatted
        assert "Tool: Bash(" in formatted
        assert "pytest" in formatted
        Path(path).unlink()

    def test_handles_tool_result_content(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "Run tests"}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_123",
                                    "name": "Bash",
                                    "input": {"command": "pytest"},
                                }
                            ]
                        },
                    }
                )
                + "\n"
            )
            # Tool result comes back as a "user" message with array content
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_123",
                                    "content": "5 passed, 0 failed",
                                }
                            ]
                        },
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "All tests pass!"}]},
                    }
                )
                + "\n"
            )
            path = f.name

        turns, _, _ = _extract_turns(path)
        assert len(turns) == 1  # All part of same turn
        formatted = turns[0].format()
        assert "User: Run tests" in formatted
        assert "Tool: Bash(" in formatted
        assert "Tool Result (toolu_123): 5 passed, 0 failed" in formatted
        assert "Assistant: All tests pass!" in formatted
        Path(path).unlink()

    def test_skips_progress_and_system_types(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "Hello"}}) + "\n")
            f.write(json.dumps({"type": "progress", "data": {"type": "hook_progress"}}) + "\n")
            f.write(json.dumps({"type": "system", "subtype": "stop_hook_summary"}) + "\n")
            f.write(json.dumps({"type": "file-history-snapshot", "snapshot": {}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "Hi!"}]},
                    }
                )
                + "\n"
            )
            path = f.name

        turns, _, _ = _extract_turns(path)
        assert len(turns) == 1
        formatted = turns[0].format()
        assert "User: Hello" in formatted
        assert "Assistant: Hi!" in formatted
        assert "progress" not in formatted
        assert "system" not in formatted
        assert "snapshot" not in formatted
        Path(path).unlink()

    def test_handles_empty_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name

        turns, total_chars, total_lines = _extract_turns(path)
        assert turns == []
        Path(path).unlink()

    def test_truncates_long_thinking(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"content": "Think hard"}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "thinking", "thinking": "X" * 500},
                                {"type": "text", "text": "Done"},
                            ]
                        },
                    }
                )
                + "\n"
            )
            path = f.name

        turns, _, _ = _extract_turns(path)
        formatted = turns[0].format()
        # Thinking should be truncated to ~200 chars + "..."
        assert "Thinking: " in formatted
        assert "..." in formatted
        assert len([line for line in formatted.split("\n") if line.startswith("Thinking:")][0]) < 250
        Path(path).unlink()

    def test_multi_turn_conversation(self) -> None:
        """Full realistic multi-turn conversation with all message types."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Turn 1: simple Q&A
            f.write(json.dumps({"type": "user", "message": {"content": "What is this repo?"}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "thinking", "thinking": "Let me check the repo"},
                                {
                                    "type": "tool_use",
                                    "id": "toolu_1",
                                    "name": "Bash",
                                    "input": {"command": "git log --oneline -3"},
                                },
                            ]
                        },
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_1",
                                    "content": "abc123 Initial commit",
                                }
                            ]
                        },
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "This is a new repo with one commit."}]},
                    }
                )
                + "\n"
            )
            # Skipped types interspersed
            f.write(json.dumps({"type": "system", "subtype": "stop_hook_summary"}) + "\n")
            # Turn 2: follow-up
            f.write(json.dumps({"type": "user", "message": {"content": "Fix the bug"}}) + "\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "Bug fixed!"}]},
                    }
                )
                + "\n"
            )
            path = f.name

        turns, _, total_lines = _extract_turns(path)
        assert len(turns) == 2
        assert total_lines == 7

        # Turn 1 should have thinking, tool use, tool result, and text
        t1 = turns[0].format()
        assert "User: What is this repo?" in t1
        assert "Thinking:" in t1
        assert "Tool: Bash(" in t1
        assert "Tool Result (toolu_1):" in t1
        assert "Assistant: This is a new repo with one commit." in t1

        # Turn 2 should be simple
        t2 = turns[1].format()
        assert "User: Fix the bug" in t2
        assert "Assistant: Bug fixed!" in t2
        Path(path).unlink()


class TestShouldSummarize:
    def test_meets_exchange_threshold(self) -> None:
        assert _should_summarize(new_turn_count=3, new_chars=100, min_exchanges=3, min_chars=2000) is True

    def test_meets_chars_threshold(self) -> None:
        assert _should_summarize(new_turn_count=1, new_chars=2000, min_exchanges=3, min_chars=2000) is True

    def test_meets_both_thresholds(self) -> None:
        assert _should_summarize(new_turn_count=5, new_chars=5000, min_exchanges=3, min_chars=2000) is True

    def test_below_both_thresholds(self) -> None:
        assert _should_summarize(new_turn_count=1, new_chars=100, min_exchanges=3, min_chars=2000) is False


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
    def test_formats_turns(self) -> None:
        turns = [
            Turn(entries=["User: Hello", "Assistant: Hi there"]),
            Turn(entries=["User: Fix bug", "Assistant: Done"]),
        ]
        result = _format_transcript_for_summary(turns, max_chars=10000)
        assert "--- Turn 1 ---" in result
        assert "--- Turn 2 ---" in result
        assert "User: Hello" in result
        assert "Assistant: Done" in result

    def test_truncates_from_start(self) -> None:
        turns = [
            Turn(entries=["User: " + "A" * 1000, "Assistant: " + "B" * 1000]),
            Turn(entries=["User: Recent question", "Assistant: Recent answer"]),
        ]
        result = _format_transcript_for_summary(turns, max_chars=200)
        assert "truncated" in result
        # Recent content should be preserved
        assert "Recent answer" in result


class TestBuildSummaryPrompt:
    def test_includes_metadata(self) -> None:
        turns = [Turn(entries=["User: Hello", "Assistant: Hi"])]
        prompt = _build_summary_prompt(turns, "myproject", "/home/user/myproject", "sess-1", 10000)
        assert "myproject" in prompt
        assert "sess-1" in prompt
        assert "Turns: 1" in prompt
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


class TestPersistTurns:
    def test_persists_turns(self, mock_sync_letta: MagicMock) -> None:
        turns = [
            Turn(entries=["User: Hello", "Assistant: Hi there"]),
            Turn(entries=["User: Fix bug", "Assistant: Done"]),
        ]

        _persist_turns(mock_sync_letta, "archive-001", turns, "sess-1", "myproject")

        assert mock_sync_letta.archives.passages.create.call_count == 2

        # Check first call
        call_args = mock_sync_letta.archives.passages.create.call_args_list[0]
        assert call_args[0][0] == "archive-001"
        assert "User: Hello" in call_args[1]["text"]
        assert "Assistant: Hi there" in call_args[1]["text"]
        assert call_args[1]["tags"] == ["exchange"]
        assert call_args[1]["metadata"]["session_id"] == "sess-1"
        assert call_args[1]["metadata"]["project"] == "myproject"


def _make_transcript(turns: int = 4, include_tool_use: bool = False) -> str:
    """Create a temporary transcript file with the given number of turns.

    Uses realistic Claude Code JSONL format with type="user" and type="assistant".
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for i in range(turns):
            # User message
            f.write(json.dumps({"type": "user", "message": {"content": f"Question {i + 1}"}}) + "\n")

            if include_tool_use and i % 2 == 0:
                # Tool use + result for every other turn
                f.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": f"toolu_{i}",
                                        "name": "Bash",
                                        "input": {"command": f"echo answer_{i}"},
                                    }
                                ]
                            },
                        }
                    )
                    + "\n"
                )
                f.write(
                    json.dumps(
                        {
                            "type": "user",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": f"toolu_{i}",
                                        "content": f"answer_{i}",
                                    }
                                ]
                            },
                        }
                    )
                    + "\n"
                )

            # Assistant text response
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": f"Answer {i + 1}"}]},
                    }
                )
                + "\n"
            )
        return f.name


class TestStop:
    def test_persists_raw_turns_with_tags(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """Layer 1: turns are persisted with correct tags/metadata."""
        transcript_path = _make_transcript(turns=2)

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

            # 2 turns should have been persisted
            assert mock_sync_letta.archives.passages.create.call_count == 2

            # Check tags on first call
            call_args = mock_sync_letta.archives.passages.create.call_args_list[0]
            assert call_args[1]["tags"] == ["exchange"]
            assert call_args[1]["metadata"]["session_id"] == "sess-1"

            # Verify turn content includes user and assistant
            text = call_args[1]["text"]
            assert "User: Question 1" in text
            assert "Assistant: Answer 1" in text

        Path(transcript_path).unlink()

    def test_persists_turns_with_tool_use(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """Layer 1: turns with tool_use and tool_result are fully captured."""
        transcript_path = _make_transcript(turns=1, include_tool_use=True)

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

            assert mock_sync_letta.archives.passages.create.call_count == 1
            text = mock_sync_letta.archives.passages.create.call_args[1]["text"]
            assert "User: Question 1" in text
            assert "Tool: Bash(" in text
            assert "Tool Result (toolu_0):" in text
            assert "Assistant: Answer 1" in text

        Path(transcript_path).unlink()

    def test_persists_turns_below_summary_threshold(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """Layer 1 works even without Layer 2 triggering."""
        transcript_path = _make_transcript(turns=1)

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

            # Turn should still be persisted (Layer 1)
            assert mock_sync_letta.archives.passages.create.call_count == 1
            call_args = mock_sync_letta.archives.passages.create.call_args
            persisted_text = call_args[1]["text"] if "text" in call_args[1] else call_args[0][1]
            assert "Question 1" in persisted_text
            assert call_args[1]["tags"] == ["exchange"]

        Path(transcript_path).unlink()

    def test_summarizes_above_threshold(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """Both Layer 1 (turns) and Layer 2 (summary) fire above threshold."""
        transcript_path = _make_transcript(turns=4)

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

            # 4 turn passages + 1 summary passage = 5 total
            assert mock_sync_letta.archives.passages.create.call_count == 5

            # Last call should be the summary with tags
            summary_call = mock_sync_letta.archives.passages.create.call_args_list[-1]
            summary_text = summary_call[1]["text"] if "text" in summary_call[1] else summary_call[0][1]
            assert "LLM summary of session" in summary_text
            assert summary_call[1]["tags"] == ["summary"]

        Path(transcript_path).unlink()

    def test_summarization_failure_logs_error(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """When summarization fails, turns are already persisted, error is logged."""
        transcript_path = _make_transcript(turns=4)

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

            # Turns should still be persisted (4 calls)
            assert mock_sync_letta.archives.passages.create.call_count == 4

            # Error should be logged
            mock_logger.error.assert_any_call("Summarization failed — raw turns already persisted")

        Path(transcript_path).unlink()

    def test_letta_failure_logs_error(self, mock_sync_letta: MagicMock, tmp_path: Path) -> None:
        """Connection failure logs error and returns early."""
        transcript_path = _make_transcript(turns=2)

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
