# yaucca v2: Cloud-Native Architecture

## Status

**Code: complete. Deployment and end-to-end testing: not done.**

All Phase 1 code is written and has 82 passing unit tests. Nothing has been
deployed to Modal, no data has been migrated from Letta, and no end-to-end
verification has been performed. See `modal-deployment-plan.md` for the
remaining work.

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

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    yaucca-cloud                              │
│                   (Modal.com)                                │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐                        │
│  │  FastAPI      │  │  Bearer Auth │                        │
│  │  HTTP API     │  │  Middleware   │                        │
│  └──────┬───────┘  └──────────────┘                        │
│         │                                                   │
│  ┌──────▼──────────┐     ┌──────────────────┐              │
│  │  Storage Layer   │     │  Embedding Layer  │              │
│  │  (SQLite +       │     │  (OpenAI text-    │              │
│  │   sqlite-vec)    │     │   embedding-3-    │              │
│  │                  │     │   small, 1536d)   │              │
│  └─────────────────┘     └──────────────────┘              │
│                                                             │
│  Persistent volume: /data/yaucca.db                         │
└─────────────────────────────────────────────────────────────┘
         ▲              ▲              ▲
         │              │              │
   ┌─────┴────┐  ┌─────┴────┐  ┌─────┴─────┐
   │  Claude   │  │ Claude.ai │  │  Claude   │
   │  Code     │  │  (web)    │  │  mobile   │
   │ (laptop)  │  │           │  │  (phone)  │
   │           │  │           │  │           │
   │ + hooks   │  │ remote    │  │ remote    │
   │ + stdio   │  │ MCP       │  │ MCP       │
   │   MCP     │  │ (Phase 2) │  │ (Phase 2) │
   └──────────┘  └──────────┘  └───────────┘
```

### Claude Code (laptop) — hooks + stdio MCP

On the laptop, yaucca runs in two modes simultaneously:

1. **Local stdio MCP server** (`mcp_server.py`) — Claude Code connects to
   this via `.mcp.json`. The 6 MCP tools proxy all calls to the cloud HTTP API.
2. **Hooks** (`hooks.py`) — SessionStart injects memory, Stop persists
   transcripts and generates summaries via `claude -p` (stays on Max billing).

Both talk to the cloud database over HTTPS.

### Claude.ai / mobile — remote MCP (Phase 2, not started)

These surfaces will connect via a remote MCP server with OAuth 2.1
authentication. No hooks — explicit tool calls only.

## Why Modal Over Fly.io

| Concern | Fly.io | Modal |
|---------|--------|-------|
| Billing model | Always-on VM (~$3-5/mo) | Per-second, scale-to-zero ($0 when idle) |
| Persistent storage | Fly Volumes (traditional FS) | Modal Volumes (commit/reload model) |
| Deployment | Dockerfile + `fly deploy` | Pure Python decorators + `modal deploy` |
| Infra config | fly.toml, Dockerfile, Procfile | All in Python code — no YAML/Docker |
| Scaling | Manual machine sizing | Automatic, including to zero |

Modal is ideal for yaucca: single user, low traffic, scale-to-zero means
near-zero cost, and no Docker/infra files to maintain.

## Database Schema

Single SQLite database using sqlite-vec for vector search.

```sql
CREATE TABLE blocks (
    label       TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    value       TEXT NOT NULL DEFAULT '',
    char_limit  INTEGER NOT NULL DEFAULT 5000,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE passages (
    id          TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    tags        TEXT DEFAULT '[]',   -- JSON array: ["exchange"], ["summary"]
    metadata    TEXT DEFAULT '{}',   -- JSON object: session_id, project, etc.
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE passages_vec USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[1536]           -- OpenAI text-embedding-3-small
);
```

## HTTP API

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

Bearer token auth on all routes except `/health`.

## MCP Tool Interface

The 6 MCP tools are unchanged in interface from v1. Implementation changed from
Letta API calls to cloud HTTP API calls:

| Tool | v1 (Letta) | v2 (Cloud API) |
|------|-----------|----------------|
| `get_memory_block(name)` | `blocks.retrieve()` | `GET /api/blocks/{name}` |
| `update_memory_block(name, value)` | `blocks.update()` | `PUT /api/blocks/{name}` |
| `list_memory_blocks()` | `blocks.list()` | `GET /api/blocks` |
| `search_archival_memory(query)` | `passages.search()` | `GET /api/passages/search?q=` |
| `insert_archival_memory(text)` | `archives.passages.create()` | `POST /api/passages` |
| `get_recent_messages(count)` | `passages.list()` | `GET /api/passages?tag=exchange` |

## File Structure

```
src/yaucca/
  # Core (rewritten to use cloud API)
  mcp_server.py          # stdio MCP server — proxies to cloud HTTP API
  hooks.py               # SessionStart/Stop — calls cloud HTTP API
  prompt.py              # XML rendering (unchanged from v1)
  config.py              # YAUCCA_URL + YAUCCA_AUTH_TOKEN (Letta config kept for migration)

  # Cloud server
  cloud/
    db.py                # SQLite + sqlite-vec storage layer
    server.py            # FastAPI HTTP server
    embed.py             # Embedding generation (OpenAI API or stub)
    modal_app.py         # Modal deployment definition
    migrate.py           # One-time Letta → SQLite migration script
```

## Modal Container Lifecycle

```python
@app.function(
    volumes={"/data": volume},
    scaledown_window=300,         # 5 min idle before shutdown
    secrets=[modal.Secret.from_name("yaucca-secrets")],
)
@modal.concurrent(max_inputs=10)  # one container, many async requests
@modal.asgi_app()
def serve():
    return create_app(db_path="/data/yaucca.db", on_write=volume.commit)
```

- **Cold start**: Container spins up, opens SQLite from volume, serves requests
- **Warm**: Handles requests with in-memory SQLite connection
- **Idle**: After `scaledown_window` seconds, container shuts down
- **Volume sync**: `volume.commit()` after every write operation

## What We Lose (vs Letta)

- Letta ecosystem and future Letta features
- Shared Letta server with other agents (Nameless would need its own solution)
- Letta's embedding infrastructure — we manage our own

## What We Gain

- Phone access via Claude mobile (Phase 2)
- No Docker dependency — SQLite is embedded
- Full control over storage layer, schema, and embedding strategy
- Simpler deployment — single Python process + SQLite file
- Cost: ~$0-1/month (Modal scale-to-zero) vs ~$3-5/month (Fly.io always-on)
- Faster: SQLite is faster than Letta's HTTP API for our access patterns
