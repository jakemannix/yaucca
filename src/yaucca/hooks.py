"""Claude Code hook scripts for yaucca's stateful lifecycle.

Three subcommands:

  session_start — Fired on SessionStart (startup, resume, compact, clear).
                  Queries yaucca cloud for memory blocks + recent tagged passages,
                  renders XML context, and outputs to stdout as additionalContext.

  stop          — Fired on Stop (after each assistant turn completes).
                  Layer 1 only: persists raw turns as individual archival passages
                  tagged "exchange". Cheap HTTP POSTs, no LLM calls.

  session_end   — Fired on SessionEnd (when the session actually closes).
                  Forks a detached background worker (survives Claude Code exit)
                  that runs Layers 2+3: a single `claude -p` call generates both
                  an archival summary and a compact context block update.
                  Logs to ~/.yaucca/session_end.log.

All use httpx to call the yaucca cloud HTTP API.
All diagnostic logging goes to stderr so stdout stays clean for Claude Code.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from yaucca.config import SummarizationConfig, get_settings
from yaucca.prompt import RECALL_PASSAGE_LIMIT, render_full_context

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="yaucca: %(message)s")
logger = logging.getLogger("yaucca.hooks")

# State directories
STATE_DIR = Path.home() / ".yaucca"
SESSIONS_DIR = STATE_DIR / "sessions"


# --- Data structures ---


@dataclass
class Turn:
    """A conversation turn: user input + all responses, tool calls, and results."""

    entries: list[str]  # Formatted text lines

    def format(self) -> str:
        return "\n".join(self.entries)


@dataclass
class SessionState:
    """Tracks persistence state for a session across stop hook invocations."""

    session_id: str
    last_persisted_line_offset: int = 0  # Layer 1: raw exchanges
    last_summary_ts: str = ""  # Layer 2: summarization
    last_summary_exchange_count: int = 0
    last_summary_line_offset: int = 0
    last_summary_passage_id: str = ""


# --- Cloud API client ---


def _cloud_client() -> tuple[httpx.Client, str]:
    """Create an httpx client configured for the yaucca cloud API.

    Returns (client, base_url).
    """
    settings = get_settings()
    base_url = settings.cloud.url
    headers: dict[str, str] = {}
    if settings.cloud.auth_token:
        headers["Authorization"] = f"Bearer {settings.cloud.auth_token}"
    client = httpx.Client(base_url=base_url, headers=headers, timeout=15.0)
    return client, base_url


def _read_stdin_json() -> dict[str, Any]:
    """Read and parse JSON from stdin."""
    try:
        data = sys.stdin.read()
        if not data.strip():
            return {}
        return json.loads(data)
    except (json.JSONDecodeError, OSError):
        return {}


# --- Session state persistence ---


def _load_session_state(session_id: str) -> SessionState:
    """Load persisted session state, or return fresh defaults."""
    state_file = SESSIONS_DIR / f"{session_id}.json"
    try:
        if state_file.exists():
            data = json.loads(state_file.read_text())
            return SessionState(**data)
    except Exception:
        pass
    return SessionState(session_id=session_id)


def _save_session_state(state: SessionState) -> None:
    """Persist session state to disk."""
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        state_file = SESSIONS_DIR / f"{state.session_id}.json"
        from dataclasses import asdict

        state_file.write_text(json.dumps(asdict(state)))
    except Exception as e:
        logger.debug("Failed to save session state: %s", e)


# --- Transcript extraction ---

# Max chars for thinking block preview in turn output
_THINKING_PREVIEW_CHARS = 200
# Max chars for tool input preview in turn output
_TOOL_INPUT_PREVIEW_CHARS = 200


def _extract_turns(transcript_path: str, start_line: int = 0) -> tuple[list[Turn], int, int]:
    """Read transcript JSONL and extract all conversation turns.

    A turn starts with a user text message and includes all subsequent
    assistant responses (text, thinking, tool_use) and tool results until
    the next user text message.

    Args:
        transcript_path: Path to the JSONL transcript file.
        start_line: Line offset to start reading from (0-indexed).

    Returns:
        (turns, total_new_chars, total_lines) where total_lines is the
        total number of lines in the file (for tracking offset).
    """
    path = Path(transcript_path)
    if not path.exists():
        return [], 0, 0

    try:
        all_lines = path.read_text().strip().split("\n")
    except Exception:
        return [], 0, 0

    total_lines = len(all_lines)
    lines_to_process = all_lines[start_line:]

    turns: list[Turn] = []
    total_chars = 0
    current_entries: list[str] | None = None

    for line in lines_to_process:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = entry.get("type", "")

        if msg_type == "user":
            content = entry.get("message", {}).get("content", "")

            if isinstance(content, str) and content.strip():
                # User text message — start a new turn
                if current_entries is not None:
                    turns.append(Turn(entries=current_entries))
                current_entries = [f"User: {content}"]
                total_chars += len(content)

            elif isinstance(content, list):
                # Tool results — append to current turn
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "tool_result":
                        tool_id = item.get("tool_use_id", "?")
                        result_content = item.get("content", "")
                        if isinstance(result_content, str) and result_content.strip():
                            entry_text = f"Tool Result ({tool_id}): {result_content}"
                            if current_entries is not None:
                                current_entries.append(entry_text)
                            total_chars += len(result_content)

        elif msg_type == "assistant":
            content = entry.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "")

                if item_type == "text":
                    text = item.get("text", "")
                    if text.strip():
                        entry_text = f"Assistant: {text}"
                        if current_entries is not None:
                            current_entries.append(entry_text)
                        total_chars += len(text)

                elif item_type == "thinking":
                    thinking = item.get("thinking", "")
                    if thinking.strip():
                        preview = thinking[:_THINKING_PREVIEW_CHARS]
                        if len(thinking) > _THINKING_PREVIEW_CHARS:
                            preview += "..."
                        entry_text = f"Thinking: {preview}"
                        if current_entries is not None:
                            current_entries.append(entry_text)
                        total_chars += len(preview)

                elif item_type == "tool_use":
                    name = item.get("name", "?")
                    tool_input = item.get("input", {})
                    input_str = json.dumps(tool_input) if tool_input else ""
                    if len(input_str) > _TOOL_INPUT_PREVIEW_CHARS:
                        input_str = input_str[:_TOOL_INPUT_PREVIEW_CHARS] + "..."
                    entry_text = f"Tool: {name}({input_str})"
                    if current_entries is not None:
                        current_entries.append(entry_text)
                    total_chars += len(entry_text)

        # Skip progress, system, file-history-snapshot

    # Flush final turn
    if current_entries is not None:
        turns.append(Turn(entries=current_entries))

    return turns, total_chars, total_lines


# --- Summarization ---


def _format_transcript_for_summary(turns: list[Turn], max_chars: int) -> str:
    """Format turns for LLM summarization, truncating from the start to keep recent context."""
    parts: list[str] = []
    for i, turn in enumerate(turns, 1):
        parts.append(f"--- Turn {i} ---\n{turn.format()}\n")

    full_text = "\n".join(parts)

    if len(full_text) <= max_chars:
        return full_text

    # Truncate from the start to keep the most recent turns
    truncated = full_text[-max_chars:]
    # Find first complete turn boundary after truncation
    boundary = truncated.find("--- Turn ")
    if boundary > 0:
        truncated = truncated[boundary:]
    return f"[... earlier turns truncated ...]\n{truncated}"


def _build_summary_prompt(
    turns: list[Turn],
    project_name: str,
    cwd: str,
    session_id: str,
    max_chars: int,
) -> str:
    """Build the prompt for claude -p to summarize a session and generate a context block.

    Returns a prompt that asks for JSON with two fields:
      - summary: archival session summary (~500 words)
      - context: compact orientation block for next session (3-8 lines)
    """
    now = datetime.now(UTC).strftime("%Y-%m-%d")
    transcript = _format_transcript_for_summary(turns, max_chars)
    return f"""Analyze this Claude Code session and produce TWO outputs as a JSON object.

Project: {project_name}
Working directory: {cwd}
Session ID: {session_id}
Turns: {len(turns)}

Return a JSON object with exactly two keys:

1. "summary" — A concise session summary for archival memory. Focus on:
   - What the user wanted to accomplish (goals)
   - What was actually done (work completed)
   - Key decisions made and their rationale
   - Any unfinished work or next steps
   Keep under 500 words. Use bullet points. Start with a one-line summary.

2. "context" — A compact orientation block (3-8 lines) loaded at the START of the
   next conversation. Use this exact format:
   Session: {now}. <one-line description of what repo/project was active>

   ## Previous session recap
   - <2-4 bullet points of what was accomplished>

   ## Current state
   - <1-2 bullets: what's in progress, what's next, any blockers>

Output ONLY valid JSON — no markdown fences, no preamble, no explanation.

--- Transcript ---
{transcript}"""


def _summarize_with_claude(prompt: str, summary_config: SummarizationConfig) -> str | None:
    """Call claude -p to summarize the session.

    Returns the summary text, or None on any failure.
    """
    claude_cmd = summary_config.claude_command
    if not shutil.which(claude_cmd):
        logger.debug("claude CLI not found at %r", claude_cmd)
        return None

    cmd = [claude_cmd, "-p"]
    if summary_config.model:
        cmd.extend(["--model", summary_config.model])

    # Build clean env: strip CLAUDECODE* and CLAUDE_CODE_ENTRYPOINT to prevent
    # claude from refusing to start inside another session, and set
    # YAUCCA_SKIP_HOOKS=1 to prevent recursion (stop hook) and skip SessionStart
    # (sub-agent doesn't need memory context).
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE") and k != "CLAUDE_CODE_ENTRYPOINT"}
    env["YAUCCA_SKIP_HOOKS"] = "1"

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=summary_config.timeout,
            env=env,
        )
        if result.returncode != 0:
            logger.debug("claude -p exited with code %d: %s", result.returncode, result.stderr[:200])
            return None
        summary = result.stdout.strip()
        return summary if summary else None
    except subprocess.TimeoutExpired:
        logger.warning("claude -p timed out after %ds", summary_config.timeout)
        return None
    except Exception as e:
        logger.warning("claude -p failed: %s", e)
        return None


# --- Threshold check ---


def _should_summarize(new_turn_count: int, new_chars: int, min_exchanges: int, min_chars: int) -> bool:
    """Check if new activity meets the threshold for summarization.

    Uses OR logic: either threshold being met triggers summarization.
    """
    return new_turn_count >= min_exchanges or new_chars >= min_chars


def _parse_summary_response(raw: str) -> tuple[str | None, str | None]:
    """Parse the combined JSON response from claude -p into (summary, context).

    Handles both clean JSON and JSON wrapped in markdown fences.
    Returns (summary, context) — either may be None if parsing fails.
    """
    text = raw.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (```json and ```)
        lines = [line for line in lines[1:] if not line.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
        summary = data.get("summary")
        context = data.get("context")
        return (
            summary if isinstance(summary, str) and summary.strip() else None,
            context if isinstance(context, str) and context.strip() else None,
        )
    except (json.JSONDecodeError, AttributeError):
        # Fallback: treat entire response as a plain-text summary, no context
        logger.warning("Failed to parse JSON from claude -p — using raw text as summary")
        return (text if text else None, None)


# --- Passage persistence ---


def _persist_turns(
    client: httpx.Client,
    turns: list[Turn],
    session_id: str,
    project_name: str,
) -> None:
    """Persist each turn as an individual passage tagged for filtering."""
    for turn in turns:
        text = turn.format()
        resp = client.post(
            "/api/passages",
            json={
                "text": text,
                "tags": ["exchange"],
                "metadata": {"session_id": session_id, "project": project_name},
            },
        )
        resp.raise_for_status()


def _persist_summary(
    client: httpx.Client,
    summary: str,
    previous_passage_id: str,
    session_id: str,
    project_name: str,
) -> str | None:
    """Persist an LLM-generated summary, replacing previous if exists.

    Returns the new passage ID, or None on failure.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    text = f"[{now}] Session summary for {project_name} (session {session_id})\n\n{summary}"

    try:
        # Delete previous summary passage for this session if it exists
        if previous_passage_id:
            try:
                client.delete(f"/api/passages/{previous_passage_id}")
            except Exception as e:
                logger.debug("Failed to delete previous passage %s: %s", previous_passage_id, e)

        resp = client.post(
            "/api/passages",
            json={
                "text": text,
                "tags": ["summary"],
                "metadata": {"session_id": session_id, "project": project_name},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("id")

    except Exception as e:
        logger.warning("Failed to persist summary: %s", e)
        return None


# --- Passage-like adapter for prompt.py rendering ---


class _PassageLike:
    """Adapter to make cloud API passage dicts work with prompt.py's getattr-based rendering."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.text = data.get("text", "")
        self.tags = data.get("tags", [])
        self.created_at = data.get("created_at", "")
        self.id = data.get("id", "")
        self.metadata = data.get("metadata", {})


class _BlockLike:
    """Adapter to make cloud API block dicts work with prompt.py's getattr-based rendering."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.label = data.get("label", "")
        self.value = data.get("value", "")
        self.description = data.get("description", "")
        self.limit = data.get("limit", 5000)


# --- Hook handlers ---


# Canary string that must appear in Claude's context to verify the rules file loaded.
MEMORY_CANARY = "YAUCCA_MEMORY_LOADED_OK"

# Where we write dynamic memory context (auto-loaded by CC as a rules file).
RULES_CONTEXT_FILE = Path.home() / ".claude" / "rules" / "yaucca-context.md"


def _write_rules_file() -> None:
    """Query cloud for current state and write the rules file for next session.

    Called from session_end (background worker) to prepare fresh context for
    the next CC session, and from session_start as a fallback on first run.
    """
    client, _ = _cloud_client()

    resp = client.get("/api/blocks")
    resp.raise_for_status()
    blocks = [_BlockLike(b) for b in resp.json()]

    resp = client.get("/api/passages", params={"limit": RECALL_PASSAGE_LIMIT, "order": "desc"})
    resp.raise_for_status()
    body = resp.json()
    raw_passages = body["passages"] if isinstance(body, dict) and "passages" in body else body
    all_passages = [_PassageLike(p) for p in raw_passages]

    exchanges = [p for p in all_passages if "exchange" in p.tags]
    summaries = [p for p in all_passages if "summary" in p.tags]
    other = [p for p in all_passages if p not in exchanges and p not in summaries]

    context = render_full_context(
        blocks=blocks,
        exchanges=exchanges,
        summaries=summaries + other,
        archival_count=len(all_passages),
        exchange_count=len(exchanges),
    )

    RULES_CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_content = "# yaucca Dynamic Memory Context\n\n"
    file_content += f"<!-- {MEMORY_CANARY} -->\n\n"
    file_content += context
    RULES_CONTEXT_FILE.write_text(file_content)
    logger.info(
        "Wrote rules file: %d chars to %s (%d blocks, %d exchanges, %d summaries)",
        len(file_content),
        RULES_CONTEXT_FILE,
        len(blocks),
        len(exchanges),
        len(summaries + other),
    )


def session_start(hook_input: dict[str, Any]) -> None:
    """Handle SessionStart hook: verify memory context and cloud health.

    The rules file is written by session_end (previous session's background
    worker), so CC loads it before this hook fires. This hook just verifies
    the file exists, checks cloud health (fail-fast), and outputs a small
    status message via additionalContext.

    On first-ever session (no rules file), writes one as a fallback.
    """
    if os.environ.get("YAUCCA_SKIP_HOOKS"):
        return

    source = hook_input.get("source", "startup")
    logger.info("SessionStart (source=%s)", source)

    try:
        client, _ = _cloud_client()
        client.get("/health").raise_for_status()
    except Exception as e:
        logger.error("FATAL: Cloud unreachable: %s", e)
        sys.exit(1)

    if RULES_CONTEXT_FILE.exists():
        content = RULES_CONTEXT_FILE.read_text()
        has_canary = MEMORY_CANARY in content
        print(
            f"yaucca memory context loaded from rules file "
            f"({len(content)} chars, canary={'OK' if has_canary else 'MISSING'}).\n\n"
            f"CRITICAL: If you do NOT see '{MEMORY_CANARY}' elsewhere in your context "
            f"(in a rules file, NOT in this message), STOP and tell the user: "
            f"'yaucca memory injection is broken — the rules file at "
            f"{RULES_CONTEXT_FILE} was written but Claude Code did not load it. "
            f"Check that .claude/rules/ files are being loaded.'"
        )
    else:
        # First session — write rules file so it's ready for next restart
        try:
            _write_rules_file()
            print(
                f"yaucca: first session — memory context written to {RULES_CONTEXT_FILE}. "
                f"Context will be loaded on next session start. "
                f"Restart Claude Code to activate memory."
            )
        except Exception as e:
            logger.error("FATAL: Failed to write initial rules file: %s", e)
            sys.exit(1)


def stop(hook_input: dict[str, Any]) -> None:
    """Handle Stop hook: persist raw turns only (Layer 1).

    Fires after every assistant turn. Persists new exchanges as individual
    archival passages tagged "exchange". Cheap HTTP POSTs, no LLM calls.
    """
    if os.environ.get("YAUCCA_SKIP_HOOKS"):
        return

    # Prevent recursion if a stop hook is already active
    if hook_input.get("stop_hook_active", False):
        return

    transcript_path = hook_input.get("transcript_path", "")
    logger.info(
        "Stop hook: keys=%s transcript_exists=%s",
        list(hook_input.keys()),
        bool(transcript_path and Path(transcript_path).exists()),
    )
    if not transcript_path:
        logger.debug("No transcript_path in hook input")
        return

    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", "")
    project_name = Path(cwd).name if cwd else "unknown"

    # Load session state
    state = _load_session_state(session_id)

    # Extract new turns since last persistence
    new_turns, new_chars, total_lines = _extract_turns(
        transcript_path, start_line=state.last_persisted_line_offset
    )

    if not new_turns:
        logger.debug("No new turns since last persistence")
        return

    required = get_settings().cloud.required

    # Connect to cloud API
    try:
        client, _ = _cloud_client()
        # Quick health check
        client.get("/health").raise_for_status()
    except Exception as e:
        if required:
            logger.error("FATAL: Cannot persist turns (YAUCCA_REQUIRED=true): %s", e)
            sys.exit(1)
        logger.error("Failed to connect to yaucca cloud: %s", e)
        return

    # Persist raw turns
    try:
        _persist_turns(client, new_turns, session_id, project_name)
    except Exception as e:
        if required:
            logger.error("FATAL: Turn persistence failed (YAUCCA_REQUIRED=true): %s", e)
            sys.exit(1)
        logger.error("Failed to persist turns: %s", e)
        return

    # Update persisted offset
    state.last_persisted_line_offset = total_lines
    logger.info("Persisted %d raw turns to archival memory", len(new_turns))

    # Save session state
    _save_session_state(state)


def _session_end_worker(
    transcript_path: str,
    session_id: str,
    project_name: str,
    cwd: str,
) -> None:
    """Background worker for session end tasks.

    Runs in a detached subprocess so it survives Claude Code's session exit.

    1. Summarization (Layers 2+3) — if enabled and threshold met:
       calls claude -p, persists summary + updates context block.
    2. Rules file writing — always: queries cloud and writes fresh
       context to the rules file for the next CC session.
    """
    settings = get_settings()
    summary_config = settings.summary

    try:
        # Summarization (skip if disabled)
        if not summary_config.enabled:
            return

        # Extract all turns for full-session summary
        all_turns, _, total_lines = _extract_turns(transcript_path, start_line=0)
        if not all_turns:
            logger.debug("No turns to summarize")
            return

        # Check minimum threshold — skip summarization for trivially short sessions
        if not _should_summarize(
            len(all_turns), sum(len(t.format()) for t in all_turns),
            summary_config.min_exchanges, summary_config.min_chars,
        ):
            logger.info("Session too short for summarization (%d turns)", len(all_turns))
            return

        # Connect to cloud API
        try:
            client, _ = _cloud_client()
            client.get("/health").raise_for_status()
        except Exception as e:
            logger.error("Failed to connect to yaucca cloud: %s", e)
            return

        # Single claude -p call for both summary + context block
        prompt = _build_summary_prompt(
            all_turns, project_name, cwd, session_id, summary_config.max_transcript_chars
        )
        raw_response = _summarize_with_claude(prompt, summary_config)

        if not raw_response:
            logger.error("Summarization failed — raw turns were already persisted by Stop hook")
            return

        summary, context_value = _parse_summary_response(raw_response)

        # Load session state for summary passage tracking
        state = _load_session_state(session_id)

        # Layer 2: Persist the archival summary
        if summary:
            passage_id = _persist_summary(
                client,
                summary,
                state.last_summary_passage_id,
                session_id,
                project_name,
            )

            state.last_summary_ts = datetime.now(UTC).isoformat()
            state.last_summary_exchange_count = len(all_turns)
            state.last_summary_line_offset = total_lines
            if passage_id:
                state.last_summary_passage_id = passage_id

            logger.info("Persisted LLM-generated session summary (%d turns)", len(all_turns))

        # Layer 3: Update the context memory block
        if context_value:
            try:
                resp = client.put("/api/blocks/context", json={"value": context_value})
                resp.raise_for_status()
                logger.info("Updated context memory block (%d chars)", len(context_value))
            except Exception as e:
                logger.warning("Failed to update context block: %s", e)

        _save_session_state(state)

    finally:
        # Always write the rules file for next session's context
        try:
            _write_rules_file()
        except Exception as e:
            logger.error("Failed to write rules file for next session: %s", e)


def session_end(hook_input: dict[str, Any]) -> None:
    """Handle SessionEnd hook: fork a detached worker for summarization + rules file.

    Claude Code's SessionEnd has a very short timeout (~1.5s) that kills hooks
    quickly. We fork a detached subprocess that runs _session_end_worker and
    survives the parent's exit. The worker always writes the rules file (for
    next session's context) and optionally does summarization.
    """
    if os.environ.get("YAUCCA_SKIP_HOOKS"):
        return

    transcript_path = hook_input.get("transcript_path", "")
    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", "")
    project_name = Path(cwd).name if cwd else "unknown"

    logger.info("SessionEnd: forking background worker")

    # Fork a detached subprocess that calls _session_end_worker via CLI.
    # This survives Claude Code's process exit.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE") and k != "CLAUDE_CODE_ENTRYPOINT"}
    env["YAUCCA_SKIP_HOOKS"] = "1"

    # Use the same Python that's running this hook
    python = sys.executable
    worker_script = f"""\
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
from yaucca.hooks import _session_end_worker
_session_end_worker(
    transcript_path={transcript_path!r},
    session_id={session_id!r},
    project_name={project_name!r},
    cwd={cwd!r},
)
"""

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log_file = open(STATE_DIR / "session_end.log", "a")  # noqa: SIM115
        subprocess.Popen(
            [python, "-c", worker_script],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            start_new_session=True,  # Detach from parent process group
        )
        logger.info("Background worker forked successfully")
    except Exception as e:
        logger.error("Failed to fork background worker: %s", e)


def status() -> None:
    """Show recent passages and session state — for manual verification."""
    try:
        client, base_url = _cloud_client()
    except Exception as e:
        print(f"Failed to create cloud client: {e}")
        return

    try:
        resp = client.get("/api/passages", params={"limit": 20, "order": "desc"})
        resp.raise_for_status()
        body = resp.json()
        passages = body["passages"] if isinstance(body, dict) and "passages" in body else body
    except Exception as e:
        print(f"Failed to connect to yaucca cloud at {base_url}: {e}")
        return

    print(f"Cloud: {base_url}")
    print(f"Passages: {len(passages)} most recent\n")

    for p in passages:
        tags = p.get("tags", [])
        text = p.get("text", "")
        preview = text[:120].replace("\n", " ")
        if len(text) > 120:
            preview += "..."
        pid = p.get("id", "?")[:12]
        print(f"  {pid}  tags={tags}")
        print(f"    {preview}\n")

    # Show session state files
    print("Session states:")
    if SESSIONS_DIR.exists():
        for f in sorted(SESSIONS_DIR.glob("*.json"))[-5:]:
            try:
                data = json.loads(f.read_text())
                sid = data.get("session_id", "?")[:16]
                offset = data.get("last_persisted_line_offset", 0)
                print(f"  {sid}  offset={offset}  summary_count={data.get('last_summary_exchange_count', 0)}")
            except Exception:
                print(f"  {f.name}  (unreadable)")
    else:
        print("  (none)")


def main() -> None:
    """CLI entry point with session_start, stop, and status subcommands."""
    parser = argparse.ArgumentParser(prog="yaucca-hooks", description="yaucca Claude Code hooks")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("session_start", help="SessionStart hook")
    subparsers.add_parser("stop", help="Stop hook (Layer 1: persist raw turns)")
    subparsers.add_parser("session_end", help="SessionEnd hook (Layers 2+3: summarize + update context)")
    subparsers.add_parser("status", help="Show recent passages and session state")

    args = parser.parse_args()

    if args.command == "status":
        status()
    else:
        hook_input = _read_stdin_json()
        if args.command == "session_start":
            session_start(hook_input)
        elif args.command == "stop":
            stop(hook_input)
        elif args.command == "session_end":
            session_end(hook_input)


if __name__ == "__main__":
    main()
