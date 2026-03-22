# yaucca v2: Cloud-Native Architecture

## Problem Statement

yaucca v1 is tightly coupled to Claude Code on a single laptop:

- **MCP server**: stdio transport, only accessible from local Claude Code
- **Hooks**: SessionStart/Stop, only fire in local Claude Code sessions
- **Letta**: runs in local Docker container
- **Result**: memory is inaccessible from Claude.ai (web), Claude mobile app,
  or any other Claude surface

The user (Jake) needs to access GTD task lists, capture inbox items, and query
next-actions-by-context from his iPhone — without his laptop being open.

## Design Goals

1. **Multi-surface access**: same memory available from Claude Code (laptop),
   Claude.ai (web), and Claude mobile (phone)
2. **Hooks still work locally**: the automatic inject-at-start / persist-at-end
   lifecycle continues working in Claude Code on the laptop
3. **Phone access without laptop**: capture and read GTD data from phone even
   when laptop is closed/sleeping
4. **Drop Letta dependency**: replace with a simpler, self-hosted stack we fully
   control
5. **`claude -p` stays on Max**: no API billing changes for summarization

## Key Insight: Claude.ai Custom Connectors

Claude.ai supports custom remote MCP servers via Settings > Connectors:

- Add any HTTPS MCP server URL
- Requires OAuth 2.1 authentication
- Once added via web, available on mobile automatically
- Free plan: 1 connector. Pro/Max: multiple.

This means: if yaucca becomes a cloud-hosted remote MCP server with OAuth, it's
available on every Claude surface — web, mobile, and Claude Code.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    yaucca-cloud                              │
│                   (Fly.io or similar)                        │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐ │
│  │  MCP Server   │  │  HTTP API    │  │  OAuth 2.1 Layer  │ │
│  │  (HTTP+SSE)   │  │  (internal)  │  │  (PKCE, tokens)   │ │
│  └──────┬───────┘  └──────┬───────┘  └───────────────────┘ │
│         │                  │                                 │
│         └────────┬─────────┘                                │
│                  │                                           │
│         ┌───────▼────────┐     ┌──────────────────┐        │
│         │   Storage Layer │     │  Embedding Layer  │        │
│         │   (SQLite +     │     │  (API-based, e.g. │        │
│         │    sqlite-vec)  │     │   OpenAI or local)│        │
│         └────────────────┘     └──────────────────┘        │
│                                                             │
│         Persistent volume: /data/yaucca.db                  │
└─────────────────────────────────────────────────────────────┘
         ▲              ▲              ▲
         │              │              │
   ┌─────┴────┐  ┌─────┴────┐  ┌─────┴─────┐
   │  Claude   │  │ Claude.ai │  │  Claude   │
   │  Code     │  │  (web)    │  │  mobile   │
   │ (laptop)  │  │           │  │  (phone)  │
   │           │  │           │  │           │
   │ + hooks   │  │ MCP only  │  │ MCP only  │
   │ + stdio   │  │ (remote)  │  │ (remote)  │
   │   proxy   │  │           │  │           │
   └──────────┘  └──────────┘  └───────────┘
```

### Claude Code (laptop) — dual transport

On the laptop, yaucca runs in **two modes simultaneously**:

1. **Local stdio MCP server** (unchanged from v1) — Claude Code connects to
   this via `.mcp.json` as today. Fast, no auth overhead.
2. **Hooks** (unchanged from v1) — SessionStart injects memory, Stop persists
   transcripts and generates summaries via `claude -p` (stays on Max billing).

Both the local MCP server and the hooks talk to the **same cloud database**
(yaucca-cloud) over HTTPS. The local stdio server is a thin proxy that
forwards to the cloud API — or, simpler, it connects directly to the cloud
SQLite via the same HTTP API the remote MCP server exposes.

### Claude.ai / mobile — remote MCP only

These surfaces connect to yaucca-cloud as a remote MCP server via OAuth.
No hooks, no automatic lifecycle — but for quick GTD interactions (capture
an item, query a list), explicit tool calls are sufficient:

- "Add to inbox: pick up hay" → `insert_archival_memory()`
- "What's on my @Ranch list?" → `search_archival_memory()`
- "Update my projects block" → `update_memory_block()`

## Database Schema

Replace Letta with a single SQLite database using sqlite-vec for vector search.

```sql
-- Core memory blocks (replaces Letta blocks API)
-- Expect ~5 rows, each up to 10KB of text.
CREATE TABLE blocks (
    label       TEXT PRIMARY KEY,   -- "user", "projects", "patterns", etc.
    description TEXT NOT NULL,      -- one-line description for relevance
    value       TEXT NOT NULL DEFAULT '',
    char_limit  INTEGER NOT NULL DEFAULT 5000,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Archival passages (replaces Letta archives + passages API)
-- Expect hundreds to low thousands of rows.
CREATE TABLE passages (
    id          TEXT PRIMARY KEY,   -- UUID
    text        TEXT NOT NULL,
    tags        TEXT DEFAULT '[]',  -- JSON array: ["exchange"], ["summary"]
    metadata    TEXT DEFAULT '{}',  -- JSON object: session_id, project, etc.
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Vector index for semantic search (sqlite-vec virtual table)
-- Dimension depends on embedding model choice.
CREATE VIRTUAL TABLE passages_vec USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[384]            -- all-MiniLM-L6-v2 = 384 dims
);
```

### Embedding Strategy

Embeddings are generated **server-side** in yaucca-cloud. The client (MCP
tools, hooks) never touches vectors — same as today with Letta.

**Recommended model**: OpenAI `text-embedding-3-small` (1536 dims, $0.02/1M
tokens). At yaucca's scale (~100 passages/week), cost is effectively zero.

**Alternative**: `all-MiniLM-L6-v2` running locally in the cloud container
(384 dims, free, ~50MB model). Avoids any external API dependency.

**Embedding happens on**:
- `INSERT passage` → embed text, store in `passages_vec`
- `search(query)` → embed query, `SELECT` from `passages_vec` ORDER BY distance

### Search Implementation

```sql
-- Semantic search: find passages closest to query embedding
SELECT p.id, p.text, p.tags, p.metadata, p.created_at, v.distance
FROM passages p
JOIN passages_vec v ON p.id = v.id
WHERE v.embedding MATCH ?query_embedding
  AND k = ?top_k
ORDER BY v.distance;

-- Text fallback (same as Letta's passages.list with search=)
SELECT * FROM passages
WHERE text LIKE '%' || ?query || '%'
ORDER BY created_at DESC
LIMIT ?limit;

-- Tag-filtered listing (for "exchange" vs "summary" filtering)
SELECT * FROM passages
WHERE json_each.value = ?tag
ORDER BY created_at DESC
LIMIT ?limit;
```

## MCP Tool Interface

The 6 existing MCP tools remain identical in interface. The implementation
changes from Letta API calls to SQLite queries:

| Tool | Current (Letta) | v2 (SQLite) |
|------|----------------|-------------|
| `get_memory_block(name)` | `blocks.retrieve()` | `SELECT value FROM blocks WHERE label = ?` |
| `update_memory_block(name, value)` | `blocks.update()` | `UPDATE blocks SET value = ? WHERE label = ?` |
| `list_memory_blocks()` | `blocks.list()` | `SELECT * FROM blocks` |
| `search_archival_memory(query)` | `passages.search()` | vec0 similarity search |
| `insert_archival_memory(text)` | `archives.passages.create()` | `INSERT` + embed |
| `get_recent_messages(count)` | `passages.list(ascending=False)` | `SELECT ... WHERE tags LIKE '%exchange%' ORDER BY created_at DESC` |

## Hook Changes

### SessionStart hook — minimal changes

Currently reads from Letta API. Change to read from yaucca-cloud HTTP API:

```python
# Before (v1)
blocks = client.agents.blocks.list(agent_id=agent_id)
passages = client.agents.passages.list(agent_id=agent_id, limit=30)

# After (v2)
blocks = httpx.get(f"{YAUCCA_URL}/api/blocks", headers=auth).json()
passages = httpx.get(f"{YAUCCA_URL}/api/passages?limit=30&tag=exchange", headers=auth).json()
```

Output format (XML rendering) stays identical.

### Stop hook — minimal changes

Layer 1 (persist raw turns):
```python
# Before: client.archives.passages.create(archive_id, text=turn, tags=["exchange"])
# After:  httpx.post(f"{YAUCCA_URL}/api/passages", json={"text": turn, "tags": ["exchange"]})
```

Layer 2 (summarize via `claude -p`): **No change.** Still calls `claude -p`
locally, still covered by Max billing. Only the final write changes:
```python
# Before: client.archives.passages.create(archive_id, text=summary, tags=["summary"])
# After:  httpx.post(f"{YAUCCA_URL}/api/passages", json={"text": summary, "tags": ["summary"]})
```

Layer 3 (update context block): Same pattern — HTTP POST instead of Letta API.

## HTTP API (Internal)

The yaucca-cloud server exposes a simple REST API. The MCP server (both local
stdio proxy and remote HTTP+SSE) calls this API internally.

```
GET    /api/blocks              → list all blocks
GET    /api/blocks/:label       → get one block
PUT    /api/blocks/:label       → update block value
GET    /api/passages            → list passages (?tag=, ?search=, ?limit=, ?order=)
POST   /api/passages            → create passage (auto-embeds)
DELETE /api/passages/:id        → delete passage
GET    /api/passages/search?q=  → semantic vector search (?q=, ?top_k=)
GET    /health                  → health check
```

## OAuth 2.1 Layer

Required for Claude.ai custom connector registration.

**Recommended approach**: Use an OAuth proxy like Cloudflare Access or Auth0
in front of yaucca-cloud, rather than implementing OAuth from scratch. This
gives us:

- PKCE support
- Token management
- `.well-known/oauth-authorization-server` metadata
- User management (just Jake, but the spec requires it)

**Alternative**: Implement minimal OAuth 2.1 directly in the server using
a library like `authlib` (Python). More control, more code (~200 lines).

**Simplest viable auth for single-user**: The MCP spec requires OAuth 2.1,
but for a single-user self-hosted server, the practical implementation is:

1. Server generates a long-lived token at setup time
2. OAuth flow returns this token (satisfying the protocol)
3. All requests validated against this token
4. Token rotatable via CLI command

This is technically spec-compliant (OAuth with a pre-authorized grant) while
being trivial to implement and maintain.

## Deployment

### Recommended: Fly.io

- **App**: Python (FastAPI or Starlette) + sqlite-vec
- **Persistent volume**: 1GB for SQLite database (generous for text + vectors)
- **Machine**: `shared-cpu-1x`, 256MB RAM — more than enough
- **Cost**: ~$3-5/month (smallest Fly machine + volume)
- **Domain**: `yaucca.fly.dev` or custom domain with TLS

### Setup steps

```bash
# 1. Create Fly app
fly launch --name yaucca --region sea  # Seattle, close to Jake

# 2. Create persistent volume
fly volumes create yaucca_data --size 1 --region sea

# 3. Set secrets
fly secrets set YAUCCA_AUTH_TOKEN=<generated-token>
fly secrets set OPENAI_API_KEY=<for-embeddings>  # if using OpenAI embeddings

# 4. Deploy
fly deploy

# 5. Register with Claude.ai
# Settings > Connectors > Add Custom Connector
# URL: https://yaucca.fly.dev/mcp
# Configure OAuth in Advanced settings
```

### Local development

```bash
# Run cloud server locally for testing
uvicorn yaucca.cloud.server:app --port 8283

# Run MCP server in stdio mode (unchanged)
uv run python -m yaucca.mcp_server

# Run hooks (unchanged, but YAUCCA_URL points to localhost)
YAUCCA_URL=http://localhost:8283 uv run python -m yaucca.hooks session_start
```

## Migration Path

### Phase 1: Cloud database, local everything else

1. Build the SQLite + sqlite-vec storage layer
2. Build the HTTP API server
3. Deploy to Fly.io
4. Point local hooks + MCP server at cloud URL instead of Letta
5. Verify everything works identically to v1
6. Docker container (Letta) no longer needed

### Phase 2: Remote MCP server

1. Add HTTP+SSE MCP transport to the cloud server
2. Add OAuth 2.1 layer
3. Register as Claude.ai custom connector
4. Verify access from Claude.ai web and mobile

### Phase 3: GTD system

1. Design GTD-specific memory block structure (contexts, projects, next actions)
2. Add GTD-aware tools (or use existing tools with GTD conventions)
3. Configure Claude.ai project instructions for GTD behavior
4. Set up Discord as a fallback capture channel (optional)

## File Structure (Proposed)

```
src/yaucca/
  # Existing (unchanged interface, updated internals)
  mcp_server.py          # stdio MCP server — calls cloud API instead of Letta
  hooks.py               # SessionStart/Stop — calls cloud API instead of Letta
  prompt.py              # XML rendering (unchanged)
  config.py              # Updated: YAUCCA_URL replaces LETTA_BASE_URL

  # New: cloud server
  cloud/
    server.py            # FastAPI/Starlette HTTP server
    db.py                # SQLite + sqlite-vec operations
    embed.py             # Embedding generation (OpenAI API or local model)
    auth.py              # OAuth 2.1 minimal implementation
    mcp_remote.py        # HTTP+SSE MCP transport handler
    migrate.py           # One-time Letta → SQLite migration script

  # Removed
  letta_utils.py         # No longer needed
  setup_agent.py         # Replaced by DB init in cloud/db.py
```

## What We Lose

- **Letta ecosystem**: no future Letta features, no shared server with other
  agents (Nameless would need its own solution if it uses Letta)
- **Letta's embedding infrastructure**: we manage our own embeddings now

## What We Gain

- **Phone access**: full GTD from Claude mobile app
- **No Docker dependency**: SQLite is embedded, no container to manage
- **Full control**: we own the storage layer, schema, and embedding strategy
- **Simpler deployment**: single Python process + SQLite file
- **Cost**: ~$3-5/month (Fly.io) + ~$0 for embeddings at our scale
- **Faster**: SQLite is faster than Letta's HTTP API for our access patterns
