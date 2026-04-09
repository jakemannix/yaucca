# Design: Context-Aware SessionStart

**Priority**: HIGH
**Status**: Proposed

## Problem

The `context` memory block only captures the last coding session's state.
When a new session starts, the agent has no holistic view of:
- Current travel schedule
- Overdue GTD items
- What happened in the last few days
- Upcoming deadlines

This forces expensive MCP tool calls to reconstruct basic context, which
is fragile because MCP disconnects during every context compaction.

## Goals

1. SessionStart injects a rich "daily briefing" without MCP tool calls
2. Overdue `@next` items with `due:` tags surface automatically
3. Schedule/travel info persists across sessions without manual updates
4. Agent can answer "what's on my plate today?" from injected context alone

## Design

### 1. Richer SessionEnd Prompt

The `claude -p` call at SessionEnd currently focuses on coding session state.
Expand the prompt to:

- Preserve upcoming travel/schedule from the previous context block
- Capture any new GTD items or status changes from the session
- Note the current date and what's overdue
- Summarize life context, not just code context

### 2. SessionStart: Overdue Item Injection

The SessionStart hook already queries `/api/passages` for recent exchanges.
Add a new query:

```
GET /api/passages?tag=@next&has_due=true&due_before={today}
```

Server-side: new endpoint or query parameter that filters passages where:
- Tag contains `@next` (not `@done`)
- `due:YYYY-MM-DD` tag exists and date < today

Render as a `<overdue_items>` XML block in the injected context.

### 3. Upcoming Items (This Week)

Also query for items due in the next 7 days:

```
GET /api/passages?tag=@next&has_due=true&due_before={today+7}&due_after={today}
```

Render as `<upcoming_items>` XML block.

### 4. Date Awareness

SessionStart already knows the current time (it's in `<memory_metadata>`).
The hook should:
- Include day-of-week in the metadata (humans think in weekdays)
- Pass today's date to the overdue/upcoming queries
- Track `last_triage_date` in the context block

## Server Changes

### New query parameters on `GET /api/passages`

| Param | Type | Description |
|-------|------|-------------|
| `exclude_tags` | string | Comma-separated tags to exclude (e.g. `@done`) |
| `due_before` | date | Filter to passages with `due:` tag before this date |
| `due_after` | date | Filter to passages with `due:` tag after this date |

### Implementation

Parse `due:YYYY-MM-DD` from the JSON tags array using SQL:
```sql
SELECT p.* FROM passages p
WHERE EXISTS (
    SELECT 1 FROM json_each(p.tags) AS t
    WHERE t.value LIKE 'due:%'
    AND substr(t.value, 5) < ?
)
AND EXISTS (
    SELECT 1 FROM json_each(p.tags) AS t
    WHERE t.value = '@next'
)
AND NOT EXISTS (
    SELECT 1 FROM json_each(p.tags) AS t
    WHERE t.value = '@done'
)
```

## Hook Changes

### `session_start` in hooks.py

After fetching blocks and recent exchanges, also fetch:
1. Overdue items (due before today)
2. Upcoming items (due within 7 days)
3. Render both as XML sections in the injected context

### `session_end` in hooks.py

Update the `claude -p` prompt to instruct:
- Preserve any schedule/travel info from the outgoing context block
- Note overdue items that weren't addressed
- Include the date of last triage

## Migration

- No schema changes (due dates are already in tags)
- Server needs new query parameter support
- Hook changes are backward-compatible (new sections, same format)
- Existing `@next` + `due:` tagged items work as-is
