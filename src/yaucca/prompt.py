"""Memory rendering for Claude Code context injection.

Standalone functions that render memory blocks, metadata, and recall
into XML sections suitable for additionalContext in SessionStart hooks.
"""

from datetime import UTC, datetime
from typing import Any

# Ordered list of memory block labels for coding-focused agent
BLOCK_ORDER = ["user", "projects", "patterns", "learnings", "context"]

# How many recent passages to inject into context
RECALL_PASSAGE_LIMIT = 30


def render_memory_blocks(blocks: list[Any]) -> str:
    """Render the <memory_blocks> section in Letta's XML format.

    Blocks are rendered in BLOCK_ORDER. Any blocks not in BLOCK_ORDER
    are appended at the end.
    """
    block_map = {b.label: b for b in blocks}
    ordered_labels = [label for label in BLOCK_ORDER if label in block_map]
    for b in blocks:
        if b.label not in BLOCK_ORDER:
            ordered_labels.append(b.label)

    lines = [
        "<memory_blocks>",
        "The following memory blocks are currently engaged in your core memory unit:",
        "",
    ]

    for label in ordered_labels:
        block = block_map[label]
        value = block.value or ""
        chars_current = len(value)
        chars_limit = getattr(block, "limit", 5000) or 5000
        description = getattr(block, "description", None) or "None"

        lines.append(f"<{label}>")
        lines.append(f"<description>{description}</description>")
        lines.append("<metadata>")
        lines.append(f"- chars_current={chars_current}")
        lines.append(f"- chars_limit={chars_limit}")
        lines.append("</metadata>")
        lines.append("<value>")
        lines.append(value)
        lines.append("</value>")
        lines.append(f"</{label}>")
        lines.append("")

    lines.append("</memory_blocks>")
    return "\n".join(lines)


def render_memory_metadata(archival_count: int, exchange_count: int) -> str:
    """Render the <memory_metadata> section with current timestamp and counts."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %I:%M:%S %p UTC%z")
    lines = [
        "<memory_metadata>",
        f"- The current time is: {now}",
        f"- {exchange_count} previous exchanges between you and the user are stored in recall memory",
        f"- {archival_count} total memories you created are stored in archival memory",
        "</memory_metadata>",
    ]
    return "\n".join(lines)


def render_conversation_history(exchanges: list[Any]) -> str:
    """Render the <conversation_history> section from persisted exchanges.

    Each exchange is a Passage with text in "User: ...\nAssistant: ..." format.
    Rendered in chronological order (oldest first).
    """
    lines = ["<conversation_history>", "Recent conversation exchanges:", ""]

    if exchanges:
        # Reverse so oldest is first (passages come in descending order)
        for passage in reversed(exchanges):
            text = getattr(passage, "text", str(passage)) or ""
            created = getattr(passage, "created_at", None)
            if created:
                lines.append(f"[{created}] {text}")
            else:
                lines.append(text)
            lines.append("")
    else:
        lines.append("(No previous conversation exchanges found.)")
        lines.append("")

    lines.append("</conversation_history>")
    return "\n".join(lines)


def render_archival_summaries(summaries: list[Any]) -> str:
    """Render the <archival_memory> section from session summaries and other passages.

    Summaries are rendered newest-first (as returned from Letta).
    """
    lines = ["<archival_memory>", "Session summaries and archival memories:", ""]

    if summaries:
        for passage in summaries:
            text = getattr(passage, "text", str(passage)) or ""
            created = getattr(passage, "created_at", None)
            if created:
                lines.append(f"[{created}] {text}")
            else:
                lines.append(text)
            lines.append("")
    else:
        lines.append("(No archival memories found.)")
        lines.append("")

    lines.append("</archival_memory>")
    return "\n".join(lines)


def render_full_context(
    blocks: list[Any],
    exchanges: list[Any],
    summaries: list[Any],
    archival_count: int,
    exchange_count: int,
) -> str:
    """Render the complete memory context for injection into Claude Code.

    Combines memory blocks, metadata, conversation history, and archival
    summaries into a single string suitable for additionalContext output
    from a SessionStart hook.
    """
    memory_blocks_section = render_memory_blocks(blocks)
    memory_metadata_section = render_memory_metadata(archival_count, exchange_count)
    conversation_section = render_conversation_history(exchanges)
    archival_section = render_archival_summaries(summaries)

    return (
        memory_blocks_section
        + "\n\n"
        + memory_metadata_section
        + "\n\n"
        + conversation_section
        + "\n\n"
        + archival_section
    )
