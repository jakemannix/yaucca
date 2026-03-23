# yaucca v2: Deployment & Remaining Work

## Overall Status

| Phase | Status |
|-------|--------|
| Phase 1: Storage + HTTP API + Modal | Code complete, **UNTESTED** — not deployed |
| Phase 2: Remote MCP (multi-surface) | Not started |
| Phase 3: GTD System | Not started |

**UNTESTED** means: code is written and unit tests pass (82 tests), but no
deployment, no real data, no end-to-end verification by the user.

---

## Phase 1: What's Built (UNTESTED)

All Phase 1 code is written. Everything below needs deployment + manual
verification before it can be considered done.

### Cloud modules (all UNTESTED against real infra)

| Module | Lines | What it does | Unit tested? |
|--------|-------|-------------|-------------|
| `cloud/db.py` | 264 | SQLite + sqlite-vec storage (blocks, passages, vector search) | Yes |
| `cloud/server.py` | 228 | FastAPI HTTP API (REST endpoints, bearer auth, lifespan) | Yes |
| `cloud/embed.py` | 56 | OpenAI `text-embedding-3-small` embedder + stub for tests | Yes |
| `cloud/modal_app.py` | 45 | Modal app definition (volume, secrets, ASGI) | No (needs Modal) |
| `cloud/migrate.py` | 95 | Letta → SQLite migration (reads Letta API, writes cloud API) | No (needs both services) |

### Rewritten modules (all UNTESTED against cloud API)

| Module | What changed |
|--------|-------------|
| `hooks.py` | Replaced all Letta SDK calls with `httpx` calls to `YAUCCA_URL`. All 3 layers (persist turns, summarize, update context) now use cloud API. |
| `mcp_server.py` | Replaced Letta SDK with `httpx.AsyncClient` proxying to `YAUCCA_URL`. Same 6 tools, same stdio transport. |
| `config.py` | Added `CloudConfig` (`YAUCCA_URL`, `YAUCCA_AUTH_TOKEN`). Letta config kept but marked deprecated (migration only). |

### Key design decisions in the code

- **sqlite-vec is optional**: `db.py` gracefully degrades if the extension
  isn't available. Vector search falls back to text `LIKE` search.
- **Volume commit after every write**: Modal's `volume.commit()` is called
  via an `on_write` callback after each DB write. Safe for low write volume.
- **Single container**: `modal.concurrent(max_inputs=10)` with one container.
  No concurrent SQLite writer issues.
- **Embeddings are 1536-dim**: Using OpenAI `text-embedding-3-small`. Stub
  embedder (zero vectors) used in tests.

---

## Phase 1: What's Left To Do

Everything here requires manual execution and verification.

### 1. Deploy to Modal (UNTESTED)

```bash
# Set up Modal secrets (one-time)
modal secret create yaucca-secrets \
  YAUCCA_AUTH_TOKEN=<generate-a-token> \
  OPENAI_API_KEY=<your-key>

# Deploy
modal deploy src/yaucca/cloud/modal_app.py

# Verify
curl https://jakemannix--yaucca-serve.modal.run/health
```

**Blocked**: Modal CLI requires direct outbound internet access. The Claude
Code web environment's network proxy doesn't support Modal's gRPC transport
(`grpclib` over HTTP/2, which doesn't honor `HTTP_PROXY`).

### 2. Run Letta migration (UNTESTED)

```bash
YAUCCA_URL=https://<modal-url> \
YAUCCA_AUTH_TOKEN=<token> \
uv run python -m yaucca.cloud.migrate
```

Reads all blocks and passages from the running Letta instance and writes them
to the cloud API. Idempotent for blocks (overwrites), additive for passages
(creates new rows each run — run only once).

### 3. Test hooks against cloud (UNTESTED)

```bash
# Test SessionStart — should print XML memory context
YAUCCA_URL=https://<modal-url> \
YAUCCA_AUTH_TOKEN=<token> \
echo '{"source":"startup"}' | uv run python -m yaucca.hooks session_start

# Test status command — should show passages
YAUCCA_URL=https://<modal-url> \
YAUCCA_AUTH_TOKEN=<token> \
uv run python -m yaucca.hooks status
```

### 4. Test MCP server against cloud (UNTESTED)

Update `.mcp.json` to set `YAUCCA_URL` and `YAUCCA_AUTH_TOKEN` env vars, then
verify all 6 tools work in a Claude Code session.

### 5. Verify and cut over (UNTESTED)

- Confirm all memory data migrated correctly
- Run a full Claude Code session with hooks pointing at cloud
- Confirm stop hook persists turns + generates summaries
- Stop Letta Docker container
- Monitor Modal dashboard for cold start times and costs

---

## Phase 2: Remote MCP Server (Not Started)

**Goal**: Claude.ai (web) and Claude mobile access yaucca memory via a remote
MCP server registered as a custom connector.

### What needs to be built

1. **Streamable HTTP MCP transport** (`cloud/mcp_remote.py`)
   - POST `/mcp` for client→server messages
   - GET `/mcp` with SSE for server→client messages
   - Reuses the same 6 tools from the stdio MCP server
   - Mount on the existing FastAPI app

2. **OAuth 2.1 minimal implementation** (`cloud/auth.py`)
   - Required by Claude.ai custom connector spec
   - `GET /.well-known/oauth-authorization-server` → metadata
   - `GET /oauth/authorize` → authorization endpoint
   - `POST /oauth/token` → token endpoint
   - `POST /oauth/revoke` → token revocation
   - Must support PKCE (required for public clients)
   - Single-user: can pre-authorize and return a long-lived token

3. **Claude.ai connector registration**
   - Settings → Connectors → Add Custom Connector
   - URL: `https://<modal-url>/mcp`
   - Walk through OAuth flow
   - Verify tools appear in Claude.ai web and mobile

---

## Phase 3: GTD System (Not Started)

**Goal**: Use the memory infrastructure for Getting Things Done workflows,
accessible from any Claude surface — especially phone.

### Ideas (not designed yet)

- GTD structure within existing blocks or archival tags (`@phone`, `@ranch`, etc.)
- Possible new tools: `add_to_inbox`, `get_next_actions`, `complete_action`
- Claude.ai project instructions teaching GTD conventions
- Capture channels: iOS Shortcut hitting the API, Discord bot (evaluate after
  Phase 2 — mobile connector may be sufficient)

---

## Modal Operational Notes

### Cost Estimate

At yaucca's usage level (single user, sporadic access):
- **Compute**: ~0.01-0.05 CPU-hours/day → **< $1/month**
- **Volume**: 1GB included free → **$0**
- **Total**: effectively **$0-1/month**

### Idle Timeout Tuning

`scaledown_window` controls warm container lifetime after last request:

| Timeout | Cold starts/day | Monthly cost | Latency |
|---------|----------------|-------------|---------|
| 60s | Many (10-20+) | Lowest | ~1-2s cold starts |
| 300s (5 min) | Few (3-5) | Low | Mostly warm |
| 600s (10 min) | Rare (1-2) | Moderate | Almost always warm |

Currently set to 300s. Adjust if cold starts from phone are annoying.

### Volume Commit Strategy

Every write triggers `volume.commit()`. At yaucca's low write volume, the
~50-100ms overhead is negligible and data safety is more important than latency.

---

## Cleanup (After Phase 1 Verified)

- Remove `src/yaucca/setup_agent.py`
- Remove Letta SDK from `pyproject.toml` dependencies
- Update `CLAUDE.md` to describe v2 architecture
- Stop Letta Docker container
- Archive Letta data after confirming migration
