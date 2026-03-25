# yaucca Architecture

## Overview

yaucca is a persistent long-term memory system for Claude Code, deployed as
a single-container FastAPI server on Modal.com. It provides memory across all
Claude surfaces: Claude Code CLI, Claude.ai web, and Claude mobile.

## System Diagram

```
┌──────────────────────────────────────────────────────────┐
│  Modal.com (scale-to-zero, ~$0-1/month)                  │
│                                                           │
│  ┌──────────────────┐  ┌──────────────────┐              │
│  │  FastAPI          │  │  Remote MCP       │              │
│  │  REST API         │  │  (OAuth 2.1 +     │              │
│  │  (Bearer token)   │  │   GitHub login)   │              │
│  └──────┬───────────┘  └──────┬───────────┘              │
│         │                      │                          │
│  ┌──────▼──────────────────────▼──────┐                  │
│  │  SQLite + sqlite-vec               │                  │
│  │  blocks, passages, embeddings,     │                  │
│  │  OAuth state                       │                  │
│  └────────────────────────────────────┘                  │
│                                                           │
│  ┌─────────────────┐  ┌──────────────────┐              │
│  │  Qwen3-Embed-8B │  │  Async embedding  │              │
│  │  via OpenRouter  │  │  queue (batched)  │              │
│  │  1024 dims       │  │                   │              │
│  └─────────────────┘  └──────────────────┘              │
│                                                           │
│  Persistent volume: /data/yaucca.db                       │
└───────────────┬──────────────────────────────────────────┘
                │ HTTPS
    ┌───────────┼───────────┐
    │           │           │
┌───┴────┐ ┌────┴───┐ ┌─────┴────┐
│ Claude │ │ Claude │ │  Claude  │
│ Code   │ │  .ai   │ │  mobile  │
│        │ │ (web)  │ │ (phone)  │
│        │ │        │ │          │
│ hooks  │ │ remote │ │ remote   │
│+remote │ │ MCP    │ │ MCP      │
│  MCP   │ │        │ │          │
└────────┘ └────────┘ └──────────┘
```

## Two Access Paths

### REST API (hooks)

Claude Code hooks call the REST API with a Bearer token from
`~/.config/yaucca/.env`. This is deterministic — no LLM decision-making
needed for memory persistence.

| Hook | When | What | Endpoint |
|------|------|------|----------|
| SessionStart | Session opens | Inject core blocks + recent exchanges | `GET /api/blocks`, `GET /api/passages` |
| Stop | Every turn | Persist raw exchange | `POST /api/passages` |
| SessionEnd | Session closes | `claude -p` summary → archival + context update | `POST /api/passages`, `PUT /api/blocks/context` |

### Remote MCP (tools)

All Claude surfaces connect via Streamable HTTP MCP with OAuth 2.1.
GitHub login gates access — only users in `GITHUB_ALLOWED_USERS` can authorize.

| Tool | Description |
|------|-------------|
| `get_memory_block(name)` | Read a core memory block |
| `update_memory_block(name, value)` | Replace a core memory block |
| `list_memory_blocks()` | List all blocks with sizes |
| `search_archival_memory(query, count, max_chars)` | Semantic vector search with truncation |
| `get_passages(ids, max_chars, offset)` | Fetch full text of specific passages (progressive disclosure) |
| `insert_archival_memory(text)` | Store a new archival entry |
| `get_recent_messages(count)` | Recent conversation exchanges |

## Memory Model

### Core Memory (5 blocks, always loaded)

Injected into every session at startup. Character-limited.

| Block | Limit | Purpose |
|-------|-------|---------|
| `user` | 5000 | Who the user is, preferences, work style |
| `projects` | 10000 | Active projects, repos, status |
| `patterns` | 10000 | Code conventions, preferred tools |
| `learnings` | 10000 | Debugging insights, lessons learned |
| `context` | 5000 | Current session state, recent decisions |

### Archival Memory (searchable)

Long-term storage for session summaries, exchanges, and insights.
Embedded with Qwen3-Embedding-8B (1024 dims) for semantic vector search.
Progressive disclosure: search returns truncated previews, drill into
specific passages for full text.

### Recall Memory (pre-loaded)

Recent conversation exchanges injected at session start. Written by the
Stop hook on every turn. Read is a snapshot from session start — not live.

## Database Schema

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
    metadata    TEXT DEFAULT '{}',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE passages_vec_d1024 USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[1024]
);

-- OAuth state (survives cold starts)
CREATE TABLE oauth_clients (...);
CREATE TABLE oauth_codes (...);
CREATE TABLE oauth_tokens (...);
CREATE TABLE pending_auths (...);
```

## Module Map

```
src/yaucca/
  # Client (pip install yaucca)
  hooks.py               # SessionStart/Stop/SessionEnd lifecycle
  prompt.py              # XML memory rendering
  config.py              # Settings from ~/.config/yaucca/.env
  install.py             # yaucca-install: hooks + rules + MCP + user seeding
  deploy.py              # yaucca-deploy: guided Modal deployment
  deploy_secrets.py      # Push .env secrets to Modal

  # Server (pip install yaucca[deploy])
  cloud/
    server.py            # FastAPI REST API + composite app factory
    mcp_remote.py        # Remote MCP server (OAuth 2.1, 7 tools)
    oauth_provider.py    # GitHub-delegated OAuth provider
    db.py                # SQLite + sqlite-vec storage
    embed.py             # Embedding via OpenRouter
    embed_queue.py       # Async background embedding batcher
    modal_app.py         # Modal deployment definition
    backfill.py          # Re-embed passages into new profiles

  templates/
    memory-rules.md      # Template for ~/.claude/rules/yaucca-memory.md
```

## Modal Operations

### Cost

Single user, sporadic access: **~$0-1/month** (scale-to-zero).

### Container Lifecycle

- `scaledown_window=300` (5 min idle before shutdown)
- Cold start: ~2-5s (SQLite open from volume)
- Warm: <1ms for reads, ~500ms for search (embedding query)
- `volume.commit()` after each embedding batch (async, non-blocking)

### Latency (warm container)

| Operation | End-to-end | Inside container |
|-----------|-----------|-----------------|
| Health check | ~400ms | <1ms |
| Block read | ~400ms | <1ms |
| Passage create | ~400ms | ~4ms (embed async) |
| Vector search | ~900ms | ~500ms (query embed) |

## OAuth Flow

1. Claude.ai/Code discovers `/.well-known/oauth-protected-resource/mcp`
2. Dynamic client registration at `/register`
3. `/authorize` redirects to GitHub OAuth
4. GitHub callback verifies user is in `GITHUB_ALLOWED_USERS`
5. Auth code exchanged for access token (24h) + refresh token (30d)
6. Tokens persisted to SQLite — survive cold starts
