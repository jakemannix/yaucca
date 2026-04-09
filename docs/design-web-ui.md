# Design: Web UI for yaucca

**Priority**: Medium
**Status**: Proposed

## Problem

Managing GTD items through conversational MCP tool calls is slow and
error-prone. There's no way to:
- See all passages at a glance, sorted/filtered by tag or due date
- Quickly mark items done or edit tags
- Triage overdue items in bulk
- Verify what the agent actually stored

## Goals

1. OAuth-locked web UI for viewing and editing yaucca passages
2. Sort/filter by tag, due date, status (@next vs @done)
3. Inline editing of text, tags, and status
4. Agent stays in sync with human edits

## Design

### Architecture

Mount a lightweight frontend on the existing FastAPI server. Reuse the
GitHub OAuth 2.1 infrastructure already in place for MCP.

```
/ui/              → SPA (served as static files or SSR)
/api/passages     → existing REST API (already has CRUD)
/api/blocks       → existing REST API
```

### Auth

Reuse the existing OAuth flow. The web UI redirects to GitHub login,
receives an access token, and uses it for all API calls. Same
`GITHUB_ALLOWED_USERS` gate as MCP.

### Views

#### Passage List (main view)
- Table: text (truncated), tags (chips), due date, created_at, status
- Filters: tag dropdown, status toggle (@next/@done/@waiting-for), overdue only
- Sort: by due date, created_at, or tag
- Bulk actions: mark done, change tags, delete

#### Passage Detail
- Full text (editable)
- Tags (add/remove chips)
- Due date (date picker, writes `due:YYYY-MM-DD` tag)
- Status toggle (@next → @done)

#### Memory Blocks
- Read/edit core memory blocks
- Character count vs limit
- Syntax highlighting for markdown content

### Tech Stack Options

**Option A: HTMX + Jinja2 (minimal)**
- Server-rendered HTML, no build step
- HTMX for inline editing and filtering
- Fits the "single FastAPI server" philosophy
- Ship as part of `yaucca[deploy]`

**Option B: Lightweight SPA (React/Preact/Solid)**
- More interactive, better for bulk operations
- Requires build step and static file serving
- Could be a separate package or bundled

Recommendation: **Option A** (HTMX) for v1. Keep it simple, no JS build.

### Agent Sync

The key challenge: if the user edits a passage in the web UI, the agent
needs to know about it.

**Approach: Timestamps + SessionStart diff**

Passages already have `created_at`. Add `updated_at`:

```sql
ALTER TABLE passages ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
CREATE TRIGGER passages_updated_at
    AFTER UPDATE ON passages
    SET updated_at = CURRENT_TIMESTAMP;
```

SessionStart hook tracks `last_session_start` timestamp. On next start:

```
GET /api/passages?updated_after={last_session_start}&limit=20
```

If any passages were modified between sessions, inject a
`<recent_edits>` section into context:

```xml
<recent_edits>
The following items were edited outside of this session
(possibly via web UI) since your last session:

- "Book rental car..." — tags changed: added @done
- "Grocery run..." — text updated
</recent_edits>
```

This way the agent knows what changed without polling.

## Implementation Plan

1. Add `updated_at` column + trigger to passages table
2. Add `?updated_after=` query param to GET /api/passages
3. Build HTMX templates for passage list + detail views
4. Mount on `/ui/` behind existing OAuth
5. Update SessionStart hook to check for recent edits
6. Ship as part of `yaucca[deploy]` (no separate install)
