"""Claude Code hook scripts for yaucca's stateful lifecycle.

Two subcommands:

  session_start — Fired on SessionStart (startup, resume, compact, clear).
                  Queries Letta for memory blocks + recent tagged passages,
                  renders XML context, and outputs to stdout as additionalContext.

  stop          — Fired on Stop (after each assistant turn completes).
                  Two-layer persistence:
                    Layer 1: Persists raw exchanges as individual archival passages
                             tagged "exchange" (always runs).
                    Layer 2: When summarization threshold is met, generates an
                             LLM summary via `claude -p` and persists it tagged
                             "summary" (non-catastrophic if it fails).

Both use the synchronous Letta client (short-lived scripts, no async benefit).
All diagnostic logging goes to stderr so stdout stays clean for Claude Code.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yaucca.config import SummarizationConfig, get_settings
from yaucca.letta_utils import extract_archive_id
from yaucca.prompt import RECALL_PASSAGE_LIMIT, render_full_context

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="yaucca: %(message)s")
logger = logging.getLogger("yaucca.hooks")

# State directories
STATE_DIR = Path.home() / ".yaucca"
SESSIONS_DIR = STATE_DIR / "sessions"


# --- Data structures ---


@dataclass
class Exchange:
    """A single user-assistant exchange from a transcript."""

    user: str
    assistant: str


@dataclass
class SessionState:
    """Tracks persistence state for a session across stop hook invocations."""

    session_id: str
    last_persisted_line_offset: int = 0  # Layer 1: raw exchanges
    last_summary_ts: str = ""  # Layer 2: summarization
    last_summary_exchange_count: int = 0
    last_summary_line_offset: int = 0
    last_summary_passage_id: str = ""


# --- Letta client ---


def _get_letta_client() -> Any:
    """Create a synchronous Letta client from settings."""
    from letta_client import Letta

    settings = get_settings()
    kwargs: dict[str, Any] = {"base_url": settings.letta.base_url}
    if settings.letta.api_key:
        kwargs["token"] = settings.letta.api_key
    return Letta(**kwargs)


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


def _extract_content(entry: dict[str, Any]) -> str:
    """Pull text content from a JSONL transcript entry."""
    content = entry.get("message", {}).get("content", "")
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return " ".join(parts)
    return str(content)


def _extract_all_exchanges(transcript_path: str, start_line: int = 0) -> tuple[list[Exchange], int, int]:
    """Read transcript JSONL and extract all user-assistant exchanges.

    Args:
        transcript_path: Path to the JSONL transcript file.
        start_line: Line offset to start reading from (0-indexed).

    Returns:
        (exchanges, total_new_chars, total_lines) where total_lines is the
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

    exchanges: list[Exchange] = []
    total_chars = 0
    pending_user: str | None = None

    for line in lines_to_process:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = entry.get("type", "")

        if msg_type == "human":
            content = _extract_content(entry)
            if content:
                pending_user = content
                total_chars += len(content)

        elif msg_type == "assistant" and pending_user is not None:
            content = _extract_content(entry)
            if content:
                exchanges.append(Exchange(user=pending_user, assistant=content))
                total_chars += len(content)
                pending_user = None

    return exchanges, total_chars, total_lines


# --- Summarization ---


def _format_transcript_for_summary(exchanges: list[Exchange], max_chars: int) -> str:
    """Format exchanges for LLM summarization, truncating from the start to keep recent context."""
    parts: list[str] = []
    for i, ex in enumerate(exchanges, 1):
        parts.append(f"--- Exchange {i} ---\nUser: {ex.user}\nAssistant: {ex.assistant}\n")

    full_text = "\n".join(parts)

    if len(full_text) <= max_chars:
        return full_text

    # Truncate from the start to keep the most recent exchanges
    truncated = full_text[-max_chars:]
    # Find first complete exchange boundary after truncation
    boundary = truncated.find("--- Exchange ")
    if boundary > 0:
        truncated = truncated[boundary:]
    return f"[... earlier exchanges truncated ...]\n{truncated}"


def _build_summary_prompt(
    exchanges: list[Exchange],
    project_name: str,
    cwd: str,
    session_id: str,
    max_chars: int,
) -> str:
    """Build the prompt for claude -p to summarize a session."""
    transcript = _format_transcript_for_summary(exchanges, max_chars)
    return f"""Summarize this Claude Code session concisely for future reference.

Project: {project_name}
Working directory: {cwd}
Session ID: {session_id}
Exchanges: {len(exchanges)}

Focus on:
1. What the user wanted to accomplish (goals)
2. What was actually done (work completed)
3. Key decisions made and their rationale
4. Any unfinished work or next steps

Keep it under 500 words. Use bullet points. Start with a one-line summary.

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
    # (sub-agent doesn't need Letta context).
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
        logger.debug("claude -p failed: %s", e)
        return None


# --- Threshold check ---


def _should_summarize(new_exchange_count: int, new_chars: int, min_exchanges: int, min_chars: int) -> bool:
    """Check if new activity meets the threshold for summarization.

    Uses OR logic: either threshold being met triggers summarization.
    """
    return new_exchange_count >= min_exchanges or new_chars >= min_chars


# --- Archive persistence ---


def _resolve_archive_id_sync(client: Any, agent_id: str) -> str | None:
    """Resolve archive_id using the sync Letta client.

    Lets connection errors propagate to caller. Returns None only when
    no passages exist yet.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        passages = client.agents.passages.list(agent_id, limit=1)
    return extract_archive_id(passages)


def _persist_exchanges(
    client: Any,
    agent_id: str,
    archive_id: str | None,
    exchanges: list[Exchange],
    session_id: str,
    project_name: str,
) -> None:
    """Persist each exchange as an individual archival passage tagged for filtering.

    No exception handling — caller is responsible.
    """
    for exchange in exchanges:
        text = f"User: {exchange.user}\nAssistant: {exchange.assistant}"
        if archive_id:
            client.archives.passages.create(
                archive_id,
                text=text,
                metadata={"session_id": session_id, "project": project_name},
                tags=["exchange"],
            )
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                client.agents.passages.create(
                    agent_id,
                    text=text,
                    tags=["exchange"],
                )


def _persist_summary(
    client: Any,
    agent_id: str,
    archive_id: str | None,
    summary: str,
    previous_passage_id: str,
    session_id: str,
    project_name: str,
) -> str | None:
    """Persist an LLM-generated summary to Letta archival, replacing previous if exists.

    Returns the new passage ID, or None on failure.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    text = f"[{now}] Session summary for {project_name} (session {session_id})\n\n{summary}"

    try:
        # Delete previous summary passage for this session if it exists
        if previous_passage_id:
            try:
                if archive_id:
                    client.archives.passages.delete(archive_id, previous_passage_id)
                else:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", DeprecationWarning)
                        client.agents.passages.delete(agent_id, previous_passage_id)
            except Exception as e:
                logger.debug("Failed to delete previous passage %s: %s", previous_passage_id, e)

        # Insert new summary
        if archive_id:
            result = client.archives.passages.create(
                archive_id,
                text=text,
                metadata={"session_id": session_id, "project": project_name},
                tags=["summary"],
            )
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                result = client.agents.passages.create(
                    agent_id,
                    text=text,
                    tags=["summary"],
                )

        # Extract passage ID from result
        if hasattr(result, "id"):
            passage_id: str = result.id
            return passage_id
        if isinstance(result, list) and result and hasattr(result[0], "id"):
            passage_id = result[0].id
            return passage_id
        return None

    except Exception as e:
        logger.warning("Failed to persist summary: %s", e)
        return None


# --- Hook handlers ---


def session_start(hook_input: dict[str, Any]) -> None:
    """Handle SessionStart hook: inject memory context into Claude Code.

    Queries Letta for all memory blocks and recent tagged passages, splits
    them into exchanges and summaries, and renders as XML for additionalContext.

    Gracefully degrades: if Letta is unreachable, outputs nothing and exits 0.
    """
    if os.environ.get("YAUCCA_SKIP_HOOKS"):
        return

    settings = get_settings()
    agent_id = settings.agent.agent_id
    if not agent_id:
        logger.warning("YAUCCA_AGENT_ID not set, skipping memory injection")
        return

    source = hook_input.get("source", "startup")
    logger.info("SessionStart (source=%s)", source)

    try:
        client = _get_letta_client()

        # Fetch memory blocks
        blocks_page = client.agents.blocks.list(agent_id)
        blocks = blocks_page.items if hasattr(blocks_page, "items") else blocks_page

        # Fetch recent archival passages
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            passages = client.agents.passages.list(
                agent_id,
                limit=RECALL_PASSAGE_LIMIT,
                ascending=False,
            )

        # Split passages by tag
        exchanges = [p for p in passages if "exchange" in (getattr(p, "tags", None) or [])]
        summaries = [p for p in passages if "summary" in (getattr(p, "tags", None) or [])]
        other = [p for p in passages if p not in exchanges and p not in summaries]

        context = render_full_context(
            blocks=list(blocks),
            exchanges=exchanges,
            summaries=summaries + other,
            archival_count=len(passages),
            exchange_count=len(exchanges),
        )

        # Output to stdout for additionalContext
        print(context)
        logger.info(
            "Injected %d blocks, %d exchanges, %d summaries",
            len(list(blocks)),
            len(exchanges),
            len(summaries + other),
        )

    except Exception as e:
        logger.warning("Failed to load memory from Letta: %s", e)
        # Graceful degradation: exit 0, no output


def stop(hook_input: dict[str, Any]) -> None:
    """Handle Stop hook: persist exchanges and optionally summarize.

    Two-layer persistence:
      Layer 1: Always persist raw exchanges as tagged archival passages.
      Layer 2: When threshold is met, generate and persist an LLM summary.
               If summarization fails, log error — exchanges are already safe.
    """
    if os.environ.get("YAUCCA_SKIP_HOOKS"):
        return

    # Prevent recursion if a stop hook is already active
    if hook_input.get("stop_hook_active", False):
        return

    settings = get_settings()
    agent_id = settings.agent.agent_id
    if not agent_id:
        return

    transcript_path = hook_input.get("transcript_path", "")
    if not transcript_path:
        logger.debug("No transcript_path in hook input")
        return

    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", "")
    project_name = Path(cwd).name if cwd else "unknown"

    # Load session state
    state = _load_session_state(session_id)

    # Layer 1: Extract new exchanges since last persistence
    new_exchanges, new_chars, total_lines = _extract_all_exchanges(
        transcript_path, start_line=state.last_persisted_line_offset
    )

    if not new_exchanges:
        logger.debug("No new exchanges since last persistence")
        return

    # Connect to Letta — fail-fast on connection errors
    try:
        client = _get_letta_client()
        archive_id = _resolve_archive_id_sync(client, agent_id)
    except Exception as e:
        logger.error("Failed to connect to Letta: %s", e)
        return

    # Layer 1: Persist raw exchanges
    try:
        _persist_exchanges(client, agent_id, archive_id, new_exchanges, session_id, project_name)
    except Exception as e:
        logger.error("Failed to persist exchanges: %s", e)
        return

    # Update persisted offset
    state.last_persisted_line_offset = total_lines
    logger.info("Persisted %d raw exchanges to archival memory", len(new_exchanges))

    # Layer 2: Check if we should do full summarization
    summary_config = settings.summary

    # Extract exchanges since last summary for threshold check
    exchanges_since_summary, chars_since_summary, _ = _extract_all_exchanges(
        transcript_path, start_line=state.last_summary_line_offset
    )

    if summary_config.enabled and _should_summarize(
        len(exchanges_since_summary), chars_since_summary, summary_config.min_exchanges, summary_config.min_chars
    ):
        # Extract ALL exchanges from start for full-session summary
        all_exchanges, _, _ = _extract_all_exchanges(transcript_path, start_line=0)

        if all_exchanges:
            prompt = _build_summary_prompt(
                all_exchanges, project_name, cwd, session_id, summary_config.max_transcript_chars
            )
            summary = _summarize_with_claude(prompt, summary_config)

            if summary:
                passage_id = _persist_summary(
                    client,
                    agent_id,
                    archive_id,
                    summary,
                    state.last_summary_passage_id,
                    session_id,
                    project_name,
                )

                state.last_summary_ts = datetime.now(UTC).isoformat()
                state.last_summary_exchange_count = len(all_exchanges)
                state.last_summary_line_offset = total_lines
                if passage_id:
                    state.last_summary_passage_id = passage_id

                logger.info("Persisted LLM-generated session summary (%d exchanges)", len(all_exchanges))
            else:
                logger.error("Summarization failed — raw exchanges already persisted")

    # Save session state (always, even if summarization was skipped/failed)
    _save_session_state(state)


def main() -> None:
    """CLI entry point with session_start and stop subcommands."""
    parser = argparse.ArgumentParser(prog="yaucca-hooks", description="yaucca Claude Code hooks")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("session_start", help="SessionStart hook")
    subparsers.add_parser("stop", help="Stop hook")

    args = parser.parse_args()
    hook_input = _read_stdin_json()

    if args.command == "session_start":
        session_start(hook_input)
    elif args.command == "stop":
        stop(hook_input)


if __name__ == "__main__":
    main()
