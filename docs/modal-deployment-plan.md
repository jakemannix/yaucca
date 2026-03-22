# yaucca v2: Modal.com Deployment Plan

This is the detailed work list for migrating yaucca to a cloud-native
architecture using [Modal.com](https://modal.com) instead of Fly.io.

## Why Modal Over Fly.io

| Concern | Fly.io | Modal |
|---------|--------|-------|
| Billing model | Always-on VM (~$3-5/mo) | Per-second, scale-to-zero ($0 when idle) |
| Cold start | N/A (always running) | Sub-second for Python apps |
| Persistent storage | Fly Volumes (traditional FS) | Modal Volumes (commit/reload model) |
| Deployment | Dockerfile + `fly deploy` | Pure Python decorators + `modal deploy` |
| Infra config | fly.toml, Dockerfile, Procfile | All in Python code — no YAML/Docker |
| HTTPS/TLS | Automatic | Automatic (`*.modal.run` or custom domain) |
| Scaling | Manual machine sizing | Automatic, including to zero |

**Modal is ideal for yaucca** because:
- Single user, low traffic — scale-to-zero means near-zero cost
- SQLite on a Volume works perfectly with a single container (no concurrent writer problem)
- No Docker/infra files to maintain — everything is Python
- `@modal.asgi_app` natively serves FastAPI with zero config

## Key Modal Architecture Decisions

### SQLite + Modal Volumes

Modal Volumes use a commit/reload model rather than a traditional filesystem.
Concurrent writes to the same file from different containers follow "last write
wins" semantics. This is **not a problem for yaucca** because:

1. Single-user system → very low write concurrency
2. We set `max_containers=1` so only one container handles requests at a time
3. `allow_concurrent_inputs=10` lets one container handle multiple async requests
4. SQLite's WAL mode + single container = safe concurrent reads/writes within process

**Pattern**: Open SQLite on the volume at container startup, commit the volume
after each write operation (or batch of writes).

### Container Lifecycle

```python
app = modal.App("yaucca")
volume = modal.Volume.from_name("yaucca-data", create_if_missing=True)

@app.function(
    volumes={"/data": volume},
    container_idle_timeout=300,   # 5 min idle before shutdown (tunable)
    allow_concurrent_inputs=10,
    secrets=[modal.Secret.from_name("yaucca-secrets")],
)
@modal.asgi_app()
def serve():
    from yaucca.cloud.server import create_app
    return create_app(db_path="/data/yaucca.db")
```

- **Cold start**: Container spins up, opens SQLite from volume, serves requests
- **Warm**: Handles requests with in-memory SQLite connection, very fast
- **Idle**: After `container_idle_timeout` seconds, container shuts down
- **Volume sync**: `volume.commit()` after writes to persist changes

### Auth for Claude.ai Connector

Claude.ai custom connectors require OAuth 2.1. Modal's built-in proxy auth
tokens use custom headers (`Modal-Key`/`Modal-Secret`), which won't satisfy
the OAuth spec. We still need to implement OAuth 2.1 ourselves in the FastAPI
app — same as the original design doc's "minimal single-user OAuth" approach.

Modal does give us HTTPS automatically on `*.modal.run`, which is a prerequisite
for OAuth. Custom domains are available on Team/Enterprise plans.

---

## Work List

### Phase 1: Storage Layer + HTTP API (Cloud Database)

**Goal**: Replace Letta with SQLite + sqlite-vec, served via FastAPI on Modal.
Local hooks and MCP server talk to the cloud API. Everything works like v1 but
without Docker/Letta.

#### 1.1 — SQLite storage module (`src/yaucca/cloud/db.py`)

- [ ] Create `src/yaucca/cloud/__init__.py`
- [ ] Implement `Database` class wrapping `sqlite3` + `sqlite-vec`
  - `init_db()` — create tables (`blocks`, `passages`, `passages_vec`) if not exist
  - `get_block(label)` / `update_block(label, value)` / `list_blocks()`
  - `create_passage(text, tags, metadata)` → returns passage with generated UUID
  - `get_passage(id)` / `delete_passage(id)`
  - `list_passages(tag=None, search=None, limit=50, order="desc")`
  - `search_passages(embedding, top_k=10)` — vec0 similarity search
- [ ] Use WAL mode for safe concurrent reads within a single process
- [ ] Add `volume_commit` callback hook so the server layer can trigger
      `volume.commit()` after writes (keeps db.py transport-agnostic)
- [ ] Unit tests with in-memory SQLite (no Modal dependency for tests)

#### 1.2 — Embedding module (`src/yaucca/cloud/embed.py`)

- [ ] Define `Embedder` protocol: `embed(text) -> list[float]`
- [ ] Implement `OpenAIEmbedder` using `text-embedding-3-small` (1536 dims)
  - Uses `httpx` async client to call OpenAI API
  - Configurable model and dimensions
- [ ] Implement `MiniLMEmbedder` as local fallback (`sentence-transformers`,
      384 dims) — useful for tests and offline dev
- [ ] Config selects embedder: `YAUCCA_EMBED_PROVIDER=openai|local`
- [ ] Unit tests with a mock/stub embedder

#### 1.3 — FastAPI HTTP server (`src/yaucca/cloud/server.py`)

- [ ] `create_app(db_path)` factory function returning a FastAPI app
- [ ] REST endpoints matching the design doc:
  ```
  GET    /api/blocks              → list all blocks
  GET    /api/blocks/{label}      → get one block
  PUT    /api/blocks/{label}      → update block value
  GET    /api/passages            → list passages (?tag=, ?search=, ?limit=, ?order=)
  POST   /api/passages            → create passage (auto-embeds)
  DELETE /api/passages/{id}       → delete passage
  GET    /api/passages/search     → semantic vector search (?q=, ?top_k=)
  GET    /health                  → health check
  ```
- [ ] Simple bearer token auth middleware (reads `YAUCCA_AUTH_TOKEN` from env)
  - `/health` is public, all other routes require `Authorization: Bearer <token>`
- [ ] Wire up volume commit after write operations
- [ ] Integration tests using `httpx.AsyncClient` with TestClient

#### 1.4 — Modal app definition (`src/yaucca/cloud/modal_app.py`)

- [ ] Define `modal.App("yaucca")`
- [ ] Define `modal.Volume.from_name("yaucca-data")`
- [ ] Define `modal.Secret.from_name("yaucca-secrets")` for auth token + OpenAI key
- [ ] `@modal.asgi_app()` function serving the FastAPI app
- [ ] Configure:
  - `volumes={"/data": volume}`
  - `container_idle_timeout=300` (5 min, tunable)
  - `allow_concurrent_inputs=10`
  - `max_containers=1` (single writer for SQLite safety)
- [ ] `volume.commit()` wired into the post-write callback from db.py
- [ ] Verify with `modal serve` locally, then `modal deploy`

#### 1.5 — Letta → SQLite migration script (`src/yaucca/cloud/migrate.py`)

- [ ] Read all blocks from Letta API → insert into `blocks` table
- [ ] Read all passages from Letta API → insert into `passages` table + embed
- [ ] Idempotent (safe to re-run): skip existing rows by ID
- [ ] CLI entrypoint: `uv run python -m yaucca.cloud.migrate`

#### 1.6 — Update config (`src/yaucca/config.py`)

- [ ] Add `YAUCCA_URL` setting (replaces `LETTA_BASE_URL`)
- [ ] Add `YAUCCA_AUTH_TOKEN` setting
- [ ] Keep old Letta settings for migration script, mark deprecated
- [ ] Validate URL format

#### 1.7 — Update hooks to use cloud API (`src/yaucca/hooks.py`)

- [ ] SessionStart: replace Letta client calls with `httpx.get(YAUCCA_URL/api/...)`
- [ ] Stop hook layer 1 (persist turns): `httpx.post(YAUCCA_URL/api/passages)`
- [ ] Stop hook layer 2 (summarize): unchanged (`claude -p` stays local)
- [ ] Stop hook layer 3 (update context): `httpx.put(YAUCCA_URL/api/blocks/context)`
- [ ] All HTTP calls include `Authorization: Bearer` header
- [ ] Prompt rendering (`prompt.py`) stays unchanged

#### 1.8 — Update MCP server to proxy through cloud API (`src/yaucca/mcp_server.py`)

- [ ] Replace Letta SDK calls with `httpx` calls to `YAUCCA_URL`
- [ ] 6 tools keep identical interface (names, params, return types)
- [ ] stdio transport unchanged — Claude Code connects same as before
- [ ] Add connection error handling with retry (cloud may be cold-starting)

#### 1.9 — End-to-end verification

- [ ] Deploy to Modal with `modal deploy`
- [ ] Run migration script to copy Letta data
- [ ] Test hooks: `YAUCCA_URL=https://yaucca--serve.modal.run uv run python -m yaucca.hooks session_start`
- [ ] Test MCP server: verify all 6 tools work through cloud API
- [ ] Confirm Letta Docker container can be stopped
- [ ] Monitor Modal dashboard for cold start times and costs

---

### Phase 2: Remote MCP Server (Multi-Surface Access)

**Goal**: Claude.ai (web) and Claude mobile can access yaucca memory via the
remote MCP server registered as a custom connector.

#### 2.1 — Streamable HTTP MCP transport (`src/yaucca/cloud/mcp_remote.py`)

- [ ] Implement MCP over HTTP with SSE (Streamable HTTP transport)
  - Claude.ai connectors use this transport
  - POST `/mcp` for client→server messages
  - GET `/mcp` with SSE for server→client messages
- [ ] Reuse the same 6 tools from the existing MCP server
- [ ] Mount on the same FastAPI app under `/mcp` path
- [ ] Test with MCP Inspector or similar tool

#### 2.2 — OAuth 2.1 minimal implementation (`src/yaucca/cloud/auth.py`)

- [ ] Implement the minimum OAuth 2.1 flow for single-user self-hosted:
  - `GET /.well-known/oauth-authorization-server` → metadata document
  - `GET /oauth/authorize` → authorization endpoint (auto-approves for single user)
  - `POST /oauth/token` → token endpoint (issues bearer token)
  - `POST /oauth/revoke` → token revocation
- [ ] Support PKCE (required by MCP spec for public clients)
- [ ] Use `authlib` or hand-roll (~200 lines for single-user case)
- [ ] Token storage: in SQLite (new `oauth_tokens` table) or in-memory
      (tokens lost on container restart, requiring re-auth — acceptable for
      single user with long-lived tokens)
- [ ] Mount OAuth routes on the FastAPI app

#### 2.3 — Claude.ai connector registration

- [ ] Deploy updated app with `modal deploy`
- [ ] Register in Claude.ai: Settings → Connectors → Add Custom Connector
  - URL: `https://<yaucca-app>.modal.run/mcp` (or custom domain)
  - Walk through OAuth flow
- [ ] Verify tools appear in Claude.ai web chat
- [ ] Verify tools appear in Claude mobile app
- [ ] Test key workflows:
  - "Add to inbox: pick up hay" → `insert_archival_memory()`
  - "What's on my @Ranch list?" → `search_archival_memory()`
  - "Show my projects block" → `get_memory_block()`

#### 2.4 — Custom domain (optional, requires Modal Team plan)

- [ ] Register domain (e.g., `yaucca.jakemann.com` or similar)
- [ ] Add CNAME record pointing to `cname.modal.domains`
- [ ] Configure in Modal workspace settings
- [ ] Update connector URL in Claude.ai settings
- [ ] Verify TLS certificate auto-provision

---

### Phase 3: GTD System

**Goal**: Use the memory infrastructure for GTD (Getting Things Done) workflows
accessible from any Claude surface — especially phone.

#### 3.1 — GTD memory block design

- [ ] Design GTD-specific structure within existing blocks, e.g.:
  - `projects` block: active projects with status and next actions
  - Add new block `inbox` or use archival memory with `inbox` tag
  - Contexts as tags: `@phone`, `@computer`, `@ranch`, `@errands`
- [ ] Or: add dedicated GTD tools beyond the core 6 (e.g., `add_to_inbox`,
      `get_next_actions`, `complete_action`)
- [ ] Document the chosen convention

#### 3.2 — GTD-aware tools (if adding new ones)

- [ ] `add_to_inbox(text)` — quick capture
- [ ] `get_next_actions(context=None)` — filtered by @context
- [ ] `process_inbox()` — review and categorize inbox items
- [ ] `complete_action(id)` — mark done
- [ ] `weekly_review()` — summary of all projects and next actions
- [ ] Add to both local MCP server and remote MCP transport

#### 3.3 — Claude.ai project instructions

- [ ] Create a Claude.ai project for GTD interactions
- [ ] Write project instructions that teach Claude the GTD conventions:
  - When user says "add to inbox" → use `insert_archival_memory` with `inbox` tag
  - When user asks about a context → search with context tag filter
  - Weekly review prompt template
- [ ] Test the full GTD workflow from phone

#### 3.4 — Capture channels (optional)

- [ ] Discord bot as fallback capture (sends to yaucca API)
- [ ] Or: iOS Shortcut that hits the yaucca API directly
- [ ] Evaluate if these are needed once Claude mobile connector works

---

## Cleanup & Removal

After Phase 1 is verified and stable:

- [ ] Remove `src/yaucca/letta_utils.py`
- [ ] Remove `src/yaucca/setup_agent.py`
- [ ] Remove Letta SDK from `pyproject.toml` dependencies
- [ ] Update `CLAUDE.md` with new architecture description
- [ ] Update `README.md`
- [ ] Stop Letta Docker container
- [ ] Archive/delete Letta data (after confirming migration is complete)

---

## Modal-Specific Operational Notes

### Cost Estimate

At yaucca's usage level (single user, sporadic access):
- **Compute**: ~0.01-0.05 CPU-hours/day → **< $1/month**
- **Volume**: 1GB included free → **$0**
- **Networking**: minimal → **$0**
- **Total**: effectively **$0-1/month** (vs $3-5/month Fly.io always-on)

### Idle Timeout Tuning

The `container_idle_timeout` controls how long a warm container stays alive
after the last request. Trade-offs:

| Timeout | Cold starts/day | Monthly cost impact | Latency |
|---------|----------------|-------------------|---------|
| 60s | Many (10-20+) | Lowest | ~1-2s cold starts |
| 300s (5 min) | Few (3-5) | Low | Mostly warm |
| 600s (10 min) | Rare (1-2) | Moderate | Almost always warm |

**Recommendation**: Start with 300s. If cold starts are annoying from phone,
bump to 600s. If cost matters, drop to 60s.

### Volume Commit Strategy

SQLite writes need to be followed by `volume.commit()` to persist to the
distributed filesystem. Options:

1. **Commit after every write** — safest, slight latency overhead (~50-100ms)
2. **Commit periodically** (every 30s) — less safe, lower latency
3. **Commit on container shutdown** — riskiest (data lost if crash), fastest

**Recommendation**: Option 1 (commit after every write). yaucca has low write
volume, so the overhead is negligible, and data safety matters.

### Monitoring

- Modal dashboard shows container starts, duration, and costs
- Add structured logging in the FastAPI app for observability
- `/health` endpoint for uptime monitoring (e.g., from UptimeRobot)
