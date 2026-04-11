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

# Maximum characters for the rendered context written to the rules file.
# With a 1M token context window, we can afford a generous budget.
# 200K chars ≈ 50K tokens, leaving plenty of room for conversation.
MAX_OUTPUT_CHARS = 200_000


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


def render_conversation_history(exchanges: list[Any], max_chars: int | None = None) -> str:
    """Render the <conversation_history> section from persisted exchanges.

    Each exchange is a Passage with text in "User: ...\nAssistant: ..." format.
    Rendered in chronological order (oldest first), keeping the most recent
    exchanges that fit within max_chars. If max_chars is None, no limit.
    """
    header = "<conversation_history>\nRecent conversation exchanges:\n\n"
    footer = "\n</conversation_history>"
    overhead = len(header) + len(footer)

    if not exchanges:
        return header + "(No previous conversation exchanges found.)\n" + footer

    # Render each exchange (passages come descending = newest first)
    rendered: list[str] = []
    for passage in exchanges:
        text = getattr(passage, "text", str(passage)) or ""
        created = getattr(passage, "created_at", None)
        entry = f"[{created}] {text}\n" if created else f"{text}\n"
        rendered.append(entry)

    # Keep most recent exchanges that fit in the budget.
    # rendered[0] is newest; we walk from newest to oldest, then reverse.
    budget = (max_chars - overhead) if max_chars is not None else None
    kept: list[str] = []
    used = 0
    for entry in rendered:
        if budget is not None and used + len(entry) > budget:
            break
        kept.append(entry)
        used += len(entry)

    if not kept:
        return header + "(Exchanges too large to fit in budget.)\n" + footer

    # Reverse to chronological order (oldest first)
    kept.reverse()
    skipped = len(exchanges) - len(kept)
    body = ""
    if skipped:
        body += f"[... {skipped} older exchanges omitted ...]\n\n"
    body += "\n".join(kept)

    return header + body + footer


def render_archival_summaries(summaries: list[Any], max_chars: int | None = None) -> str:
    """Render the <archival_memory> section from session summaries and other passages.

    Summaries come from the cloud in descending order (newest first).
    We keep the most recent that fit in the budget, then reverse to
    chronological order so the newest is at the bottom (closest to
    the current conversation).
    """
    header = "<archival_memory>\nSession summaries and archival memories:\n\n"
    footer = "\n</archival_memory>"
    overhead = len(header) + len(footer)

    if not summaries:
        return header + "(No archival memories found.)\n" + footer

    # Render each summary (input is newest-first from cloud)
    rendered: list[str] = []
    for passage in summaries:
        text = getattr(passage, "text", str(passage)) or ""
        created = getattr(passage, "created_at", None)
        entry = f"[{created}] {text}\n" if created else f"{text}\n"
        rendered.append(entry)

    # Keep most recent that fit in budget (walk newest to oldest)
    budget = (max_chars - overhead) if max_chars is not None else None
    kept: list[str] = []
    used = 0
    for entry in rendered:
        if budget is not None and used + len(entry) > budget:
            break
        kept.append(entry)
        used += len(entry)

    if not kept:
        return header + "(Summaries too large to fit in remaining budget.)\n" + footer

    # Reverse to chronological order (oldest first, newest at bottom)
    kept.reverse()
    skipped = len(summaries) - len(kept)
    body = ""
    if skipped:
        body += f"[... {skipped} older summaries omitted ...]\n\n"
    body += "\n".join(kept)

    return header + body + footer


def render_tagged_section(tag: str, passages: list[Any], max_chars: int = 20_000) -> str:
    """Render a section of passages filtered by tag.

    Used for configurable SessionStart sections (e.g. @next items, @inbox items).

    Args:
        tag: The tag name (used as section header).
        passages: Passage-like objects with .text, .tags, .created_at.
        max_chars: Character budget for this section.
    """
    if not passages:
        return ""

    header = f"<tagged_items tag=\"{tag}\">\n"
    footer = "\n</tagged_items>"
    budget = max_chars - len(header) - len(footer) - 100

    lines = []
    for p in passages:
        text = p.text.strip()
        tags = p.tags if hasattr(p, "tags") else []
        due = next((t for t in tags if t.startswith("due:")), None)
        line = f"- {text}"
        if due:
            line += f" ({due})"
        if len("\n".join(lines) + "\n" + line) > budget:
            lines.append(f"[... {len(passages) - len(lines)} more items ...]")
            break
        lines.append(line)

    return header + "\n".join(lines) + footer


def render_full_context(
    blocks: list[Any],
    exchanges: list[Any],
    summaries: list[Any],
    archival_count: int,
    exchange_count: int,
    tagged_sections: dict[str, list[Any]] | None = None,
    max_output_chars: int = MAX_OUTPUT_CHARS,
) -> str:
    """Render the complete memory context for injection into Claude Code.

    Combines memory blocks, metadata, conversation history, archival
    summaries, and tagged sections into a single string for the rules file.

    Budget strategy (to stay under max_output_chars):
    1. Memory blocks + metadata — always included (small, essential)
    2. Tagged sections — configurable, surfaced items (e.g. @next, @inbox)
    3. Conversation history — fill remaining budget with most recent exchanges
    4. Archival summaries — fill any leftover budget
    """
    memory_blocks_section = render_memory_blocks(blocks)
    memory_metadata_section = render_memory_metadata(archival_count, exchange_count)

    # Fixed overhead: blocks + metadata + separators
    fixed = memory_blocks_section + "\n\n" + memory_metadata_section + "\n\n"
    remaining = max_output_chars - len(fixed)

    # Tagged sections get budget before conversation history
    tag_sections_text = ""
    if tagged_sections:
        per_section_budget = min(20_000, remaining // (len(tagged_sections) + 2))
        for tag, passages in tagged_sections.items():
            section = render_tagged_section(tag, passages, max_chars=per_section_budget)
            if section:
                tag_sections_text += section + "\n\n"
        remaining -= len(tag_sections_text)

    # Conversation history gets priority over archival summaries.
    conversation_section = render_conversation_history(exchanges, max_chars=max(remaining - 2000, 0))
    remaining -= len(conversation_section) + 2  # +2 for "\n\n"

    archival_section = render_archival_summaries(summaries, max_chars=max(remaining, 0))

    parts = [fixed]
    if tag_sections_text:
        parts.append(tag_sections_text)
    parts.append(conversation_section)
    parts.append(archival_section)

    return "\n\n".join(parts)
