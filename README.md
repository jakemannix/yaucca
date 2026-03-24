# yaucca

**Yet Another Useless Claude Code Agent** — persistent long-term memory for
Claude Code, deployed as a cloud-native FastAPI server on
[Modal.com](https://modal.com).

Every Claude Code session starts with full memory context and ends by
persisting what happened. Memory survives across sessions, projects, and
context compactions.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Modal.com (scale-to-zero, ~$0-1/month)         │
│                                                  │
│  FastAPI + SQLite + sqlite-vec + Qwen3 embeddings│
│  Persistent volume: /data/yaucca.db              │
└────────────────┬─────────────────────────────────┘
                 │ HTTPS + Bearer token
    ┌────────────┼────────────┐
    │            │            │
┌───┴────┐ ┌────┴───┐ ┌─────┴────┐
│ Claude │ │ Claude │ │  Claude  │
│ Code   │ │  .ai   │ │  mobile  │
│(laptop)│ │ (web)  │ │ (phone)  │
│        │ │        │ │          │
│ hooks  │ │ remote │ │ remote   │
│ + MCP  │ │ MCP    │ │ MCP      │
│ (stdio)│ │(Ph. 2) │ │ (Ph. 2)  │
└────────┘ └────────┘ └──────────┘
```

### Memory Tiers

1. **Core Memory** (5 blocks, always loaded): `user`, `projects`, `patterns`,
   `learnings`, `context`
2. **Archival Memory** (searchable): Long-term storage with Qwen3-Embedding-8B
   semantic vector search (1024 dims via OpenRouter)
3. **Recall Memory** (pre-loaded): Recent conversation history injected at startup

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [Modal](https://modal.com) account (free tier works)
- OpenRouter API key (for embeddings)

### Deploy

```bash
git clone https://github.com/jakemannix/yaucca.git
cd yaucca
uv sync --extra dev

# Authenticate with Modal (one-time)
uv run --extra deploy modal setup

# Generate an auth token and create Modal secrets
YAUCCA_AUTH_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
uv run --extra deploy modal secret create yaucca-secrets \
  YAUCCA_AUTH_TOKEN="$YAUCCA_AUTH_TOKEN" \
  OPENROUTER_API_KEY=<your-key>

# Deploy
uv run --extra deploy modal deploy src/yaucca/cloud/modal_app.py

# Verify
curl https://<your-username>--yaucca-serve.modal.run/health
```

### Create `.env`

Save your credentials in `.env` (gitignored) so hooks and MCP tools pick
them up automatically:

```bash
cat > .env << EOF
YAUCCA_URL=https://<your-username>--yaucca-serve.modal.run
YAUCCA_AUTH_TOKEN=$YAUCCA_AUTH_TOKEN
EOF
```

### Install hooks

```bash
# Install SessionStart + Stop + SessionEnd hooks into ~/.claude/settings.json
uv run python -m yaucca.install

# To uninstall (restores backup):
uv run python -m yaucca.install --uninstall
```

This auto-detects the project directory and creates a backup at
`~/.claude/settings.json.bak`. Hooks read credentials from the `.env`
file — no inline env vars needed.

**Hook lifecycle:**
- **SessionStart**: Injects memory context (core blocks + recent exchanges)
- **Stop** (every turn): Persists raw exchanges to archival — cheap HTTP POSTs, no LLM calls
- **SessionEnd** (on exit): Single `claude -p` call generates both an archival summary and an updated context block

### MCP server (two options)

**Option A: Remote MCP (recommended)** — Claude.ai, mobile, and Claude Code
all connect to the same remote MCP server via OAuth 2.1 + GitHub login:

```json
// .mcp.json
{
  "mcpServers": {
    "yaucca": {
      "type": "url",
      "url": "https://jakemannix--yaucca-serve.modal.run/mcp"
    }
  }
}
```

On first connect, Claude Code opens a browser for GitHub OAuth. After that,
the token is cached and refreshed automatically.

**Option B: Stdio proxy (fallback)** — local subprocess proxies to the cloud
API via HTTP. No OAuth, uses `YAUCCA_AUTH_TOKEN` from `.env`:

```json
// .mcp.json
{
  "mcpServers": {
    "yaucca": {
      "command": "uv",
      "args": ["run", "python", "-m", "yaucca.mcp_server"],
      "env": {}
    }
  }
}
```

### Rollback

If the remote MCP server breaks, switch back to stdio:

```bash
# 1. Restore the stdio MCP config
cd /path/to/yaucca
git checkout HEAD -- .mcp.json
# Or manually: replace the "type":"url" entry with the "command":"uv" entry above

# 2. If hooks are also broken, uninstall them
uv run python -m yaucca.install --uninstall

# 3. If Modal is down, the stdio proxy will also fail (both call the cloud API).
#    As a last resort, restore the old Letta-based system:
cp ~/.claude/settings.json.bak ~/.claude/settings.json
```

The SQLite database on Modal's persistent volume is never modified by
rollback — your memory is always safe.

### Verify

```bash
cd /path/to/yaucca

# Test SessionStart hook — should print XML memory context to stdout
echo '{"source":"startup"}' | uv run python -m yaucca.hooks session_start

# If it prints nothing, check stderr for errors.
# First run may be slow (~5s) due to Modal cold start.

# Open Claude Code — it should load your memory context
claude
```

## Testing the Cutover from Letta

If you're migrating from v1 (Letta-based), follow this process. **Your Letta
data is untouched throughout — this is a copy, not a move.**

### Step 1: Migrate data

Make sure `.env` has both the cloud config and the Letta config:

```bash
# .env should contain:
# YAUCCA_URL=https://<url>.modal.run
# YAUCCA_AUTH_TOKEN=<token>
# LETTA_BASE_URL=http://localhost:8283
# YAUCCA_AGENT_ID=<your-letta-agent-id>

uv run --extra migrate python -m yaucca.cloud.migrate
```

Safe to re-run — deduplicates by text content.

### Step 2: Verify cloud data

```bash
# source .env for curl commands
source .env

# Check blocks
curl -s $YAUCCA_URL/api/blocks \
  -H "Authorization: Bearer $YAUCCA_AUTH_TOKEN" | python3 -m json.tool

# Check passage count
curl -s "$YAUCCA_URL/api/passages?limit=1000" \
  -H "Authorization: Bearer $YAUCCA_AUTH_TOKEN" | python3 -c \
  "import json,sys; print(f'{len(json.load(sys.stdin))} passages')"

# Test vector search
curl -s "$YAUCCA_URL/api/passages/search?q=test+query" \
  -H "Authorization: Bearer $YAUCCA_AUTH_TOKEN" | python3 -m json.tool

# Health (includes vec status)
curl -s $YAUCCA_URL/health \
  -H "Authorization: Bearer $YAUCCA_AUTH_TOKEN" | python3 -m json.tool
```

### Step 3: Test hooks locally (without changing your live config)

```bash
cd /path/to/yaucca

# Test SessionStart — should print XML memory from the cloud
echo '{"source":"startup"}' | uv run python -m yaucca.hooks session_start

# Check passage stats
uv run python -m yaucca.hooks status
```

### Step 4: Switch over

```bash
cd /path/to/yaucca
uv run python -m yaucca.install
```

This replaces any existing yaucca hooks with ones pointing at this repo.
A backup is saved to `~/.claude/settings.json.bak`.

### Step 5: Rollback if something breaks

The old Letta-based system is still intact. To revert:

```bash
# Option A: uninstall yaucca hooks entirely
cd /path/to/yaucca
uv run python -m yaucca.install --uninstall

# Then re-install the old Letta-based hooks from the old repo
cd /path/to/old/yetanotheruseless_claude_code_agent
uv run python -m yaucca.install

# Option B: just restore the backup
cp ~/.claude/settings.json.bak ~/.claude/settings.json
```

Verify Letta is still running: `curl http://localhost:8283/v1/health`

No data is lost — Letta still has all your original memory. Any new data
written to the cloud during testing is simply extra; it won't conflict.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `YAUCCA_URL` | *(fails fast if unset)* | Cloud server URL |
| `YAUCCA_AUTH_TOKEN` | *(none)* | Bearer token for cloud API |
| `YAUCCA_REQUIRED` | `false` | If `true`, hooks exit non-zero when cloud is unreachable |
| `YAUCCA_EMBED_BASE_URL` | `https://openrouter.ai/api/v1` | Embedding API base URL |
| `YAUCCA_EMBED_MODEL` | `qwen/qwen3-embedding-8b` | Embedding model |
| `YAUCCA_EMBED_DIMS` | `1024` | Embedding dimensions |

### Stateful Agent Mode

Set `YAUCCA_REQUIRED=true` when running yaucca as a critical dependency (e.g.
for a stateful agent that must have memory). In this mode, hooks will fail hard
(exit 1) if the cloud is unreachable, rather than silently starting without
memory. A stateful agent without memory is a different, broken thing — not a
gracefully degraded version of the same thing.

## Embedding Model Comparison

yaucca supports multiple embedding profiles for A/B testing retrieval quality
across different models or Matryoshka dimension truncations.

### How It Works

Each embedding profile creates a separate `passages_vec_{name}` table in SQLite.
When a passage is inserted, its embedding is truncated to each profile's
dimension and stored in every active profile's table. Search can target a
specific profile via `?profile=` query parameter.

### Side-by-Side Comparison

**1. Configure multiple profiles** in `modal_app.py` (or wherever you create
the `Database`):

```python
from yaucca.cloud.db import Database, EmbeddingProfile

db = Database(
    db_path="/data/yaucca.db",
    embedding_profiles=[
        EmbeddingProfile("d1024", 1024),  # full Qwen3-Embedding-8B
        EmbeddingProfile("d512", 512),    # Matryoshka half
    ],
)
```

New passages will be indexed into both profiles automatically.

**2. Backfill existing data.** If you already have passages stored, the new
profile's vec table will be empty — you must re-embed all existing passages
before the comparison is valid. Use the built-in backfill endpoint or CLI:

```bash
# Via the server endpoint (re-embeds server-side, batched):
curl -X POST "$YAUCCA_URL/api/admin/backfill?profile=d512" \
  -H "Authorization: Bearer $YAUCCA_AUTH_TOKEN"

# Or backfill all profiles at once:
curl -X POST "$YAUCCA_URL/api/admin/backfill" \
  -H "Authorization: Bearer $YAUCCA_AUTH_TOKEN"

# Or via CLI (calls the endpoint):
uv run python -m yaucca.cloud.backfill --profile d512
```

The backfill embeds in batches of 50 for efficiency. It's idempotent —
already-indexed passages are skipped.

**3. Compare search results:**

```bash
# Search with full 1024-dim profile
curl "$YAUCCA_URL/api/passages/search?q=authentication+bug&profile=d1024"

# Search with 512-dim profile
curl "$YAUCCA_URL/api/passages/search?q=authentication+bug&profile=d512"
```

**4. Drop the losing profile** once you've decided:

```python
db.drop_profile("d512")  # removes the vec table entirely
```

### Comparing Different Models

To compare two entirely different embedding models (not just Matryoshka
truncations of the same model):

1. Create a profile with a distinct name and the new model's native dimension
   (e.g., `EmbeddingProfile("openai_1536", 1536)`)
2. Update the server's embedder config to the new model
3. **Fully backfill** that profile — every passage must be embedded with the
   new model. You cannot mix embeddings from different models in the same
   vec table; cosine similarity between vectors from different embedding
   spaces is meaningless
4. Compare search results, then drop the loser's profile

## Development

```bash
uv sync --extra dev                # Install deps (includes sqlite-vec)
uv run pytest                      # Unit tests (98 tests)
uv run ruff check . && ruff format .  # Lint + format
uv run mypy src/yaucca             # Type check
```

## License

Apache-2.0
