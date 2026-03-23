# yaucca v2: Deployment & Remaining Work

## Overall Status

| Phase | Status |
|-------|--------|
| Phase 1: Storage + HTTP API + Modal | **Deployed and tested** — cutover to hooks/MCP pending |
| Phase 2: Remote MCP (multi-surface) | Not started |
| Phase 3: GTD System | Not started |

---

## Phase 1: What's Built and Verified

### Cloud modules

| Module | What it does | Tested? |
|--------|-------------|---------|
| `cloud/db.py` | SQLite + sqlite-vec storage (blocks, passages, multi-profile vector search) | Unit + deployed |
| `cloud/server.py` | FastAPI HTTP API (REST endpoints, bearer auth, diagnostics, backfill) | Unit + deployed |
| `cloud/embed.py` | Qwen3-Embedding-8B via OpenRouter (1024 dims, batch support) | Unit + deployed |
| `cloud/embed_queue.py` | Async background embedding queue (batched, debounced volume commits) | Unit + deployed |
| `cloud/modal_app.py` | Modal deployment (volume, secrets, ASGI, sqlite-vec) | Deployed |
| `cloud/migrate.py` | Letta → SQLite migration (dedup, retries, batch) | Run successfully |
| `cloud/backfill.py` | Re-embed passages into new/empty profiles (batched) | Run successfully |

### Rewritten modules

| Module | What changed | Tested against cloud? |
|--------|-------------|----------------------|
| `hooks.py` | Letta SDK → httpx to `YAUCCA_URL`. YAUCCA_REQUIRED fail-fast. | Not yet — hooks still run from old repo |
| `mcp_server.py` | Letta SDK → httpx.AsyncClient proxying to `YAUCCA_URL`. | Not yet — MCP needs env vars configured |
| `config.py` | `CloudConfig` (URL, token, required). Letta config kept for migration. | Unit tested |

### Key design decisions

- **sqlite-vec is required**: `db.py` raises on load failure (no silent fallback).
  Search returns 503 if vec unavailable.
- **Async embedding queue**: Writes return in ~4ms (text only). Embeddings
  computed in background batches. Single volume.commit() per batch.
- **Qwen3-Embedding-8B**: 1024 dims via OpenRouter (~500ms per embed,
  ~1.5s per batch of 50). Matryoshka A/B testing via named profiles.
- **YAUCCA_REQUIRED**: When true, hooks fail hard if cloud is unreachable.

### Deployment verified

The following were tested against the live Modal deployment on 2026-03-23:

- [x] `modal secret create yaucca-secrets` with auth token + OpenRouter key
- [x] `modal deploy src/yaucca/cloud/modal_app.py` — successful
- [x] Health endpoint: `GET /health` → `{"status": "ok", "vec_enabled": true, "vec_profiles": ["d1024"]}`
- [x] Bearer token auth enforced on all endpoints except `/health`
- [x] Letta migration: 5 blocks + ~450 passages migrated (dedup on re-run works)
- [x] Passage creation: ~400ms end-to-end (text written immediately, embedding queued)
- [x] Vector search: Qwen3 embeddings working, semantic results correct
- [x] Backfill: all migrated passages indexed with embeddings
- [x] Diagnostics endpoint: embed ~500ms, SQLite write ~4ms, volume.commit ~0ms (async)

### Latency profile (warm container)

| Operation | End-to-end | Inside container |
|-----------|-----------|-----------------|
| Health check | ~400ms | <1ms |
| Block read | ~400ms | <1ms |
| Passage list | ~600ms | <1ms |
| Passage create | ~400ms | ~4ms (embed async) |
| Vector search | ~900ms | ~500ms (query embed) |
| Cold start overhead | ~2-5s | N/A |

---

## Phase 1: What's Left

### Test hooks against cloud

```bash
YAUCCA_URL=https://jakemannix--yaucca-serve.modal.run \
YAUCCA_AUTH_TOKEN=<token> \
echo '{"source":"startup"}' | uv run python -m yaucca.hooks session_start

YAUCCA_URL=https://jakemannix--yaucca-serve.modal.run \
YAUCCA_AUTH_TOKEN=<token> \
uv run python -m yaucca.hooks status
```

### Test MCP server against cloud

Update `.mcp.json` to set `YAUCCA_URL` and `YAUCCA_AUTH_TOKEN` env vars, then
verify all 6 tools work in a Claude Code session.

### Cut over and verify full session

- Update `~/.claude/settings.json` hooks to point at this repo + cloud URL
- Set `"timeout": 30` on SessionStart hook (cold start tolerance)
- Run a full Claude Code session
- Confirm stop hook persists turns + generates summaries
- See README.md "Testing the Cutover from Letta" for full instructions + rollback

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

Embedding queue batches commits: after each batch of embeddings is stored,
a single `volume.commit()` persists all changes. Block writes trigger
commit via the `on_write` callback. Measured overhead: ~975ms per commit
(done in background, not blocking the caller).

---

## Cleanup (After Phase 1 Fully Verified)

- [ ] Remove Letta SDK from `pyproject.toml` optional dependencies
- [ ] Update `CLAUDE.md` to describe v2 architecture
- [ ] Stop Letta Docker container
- [ ] Archive Letta data after confirming cutover is stable
