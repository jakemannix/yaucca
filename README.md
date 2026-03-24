# yaucca

**Yet Another Useless Claude Code Agent** — persistent long-term memory for
Claude Code, deployed as a cloud-native FastAPI server on
[Modal.com](https://modal.com).

Inspired by [MemGPT/Letta](https://github.com/letta-ai/letta)'s tiered memory
architecture, but built as a lightweight self-hosted stack: SQLite + sqlite-vec
on a single Modal container with scale-to-zero billing.

Every Claude Code session starts with full memory context and ends by
persisting what happened. Memory survives across sessions, projects, and
context compactions — accessible from Claude Code (laptop), Claude.ai (web),
and Claude mobile (phone).

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Modal.com (scale-to-zero, ~$0-1/month)              │
│                                                       │
│  FastAPI + SQLite + sqlite-vec + Qwen3 embeddings     │
│  Remote MCP (OAuth 2.1 + GitHub login)                │
│  Persistent volume: /data/yaucca.db                   │
└────────────────┬──────────────────────────────────────┘
                 │ HTTPS
    ┌────────────┼────────────┐
    │            │            │
┌───┴────┐ ┌────┴───┐ ┌─────┴────┐
│ Claude │ │ Claude │ │  Claude  │
│ Code   │ │  .ai   │ │  mobile  │
│(laptop)│ │ (web)  │ │ (phone)  │
│        │ │        │ │          │
│ hooks  │ │ remote │ │ remote   │
│+remote │ │ MCP    │ │ MCP      │
│  MCP   │ │        │ │          │
└────────┘ └────────┘ └──────────┘
```

### How it works

- **Hooks** (Claude Code only): SessionStart injects memory, Stop persists
  raw exchanges, SessionEnd generates a summary via `claude -p`
- **Remote MCP** (all surfaces): 7 tools for reading/writing memory blocks,
  semantic search over archival passages, and progressive disclosure drill-down
- **OAuth 2.1**: GitHub login gates access — only allowed users can connect

### Memory tiers

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
- [OpenRouter](https://openrouter.ai) API key (for embeddings)
- [GitHub OAuth App](https://github.com/settings/developers) (for MCP auth)

### Step 1: Deploy your backend (once, from your laptop)

```bash
# Install with deployment deps
uv pip install yaucca[deploy]

# Authenticate with Modal (one-time)
modal setup

# Create a GitHub OAuth App at https://github.com/settings/developers
#   Homepage URL: https://<your-username>--yaucca-serve.modal.run
#   Callback URL: https://<your-username>--yaucca-serve.modal.run/oauth/github/callback

# Create .env with your credentials
cat > .env << 'EOF'
YAUCCA_URL=https://<your-username>--yaucca-serve.modal.run
YAUCCA_AUTH_TOKEN=<generate-with: python3 -c "import secrets; print(secrets.token_urlsafe(32))">
OPENROUTER_API_KEY=<your-openrouter-key>
YAUCCA_ISSUER_URL=https://<your-username>--yaucca-serve.modal.run
GITHUB_CLIENT_ID=<from-github-oauth-app>
GITHUB_CLIENT_SECRET=<from-github-oauth-app>
GITHUB_ALLOWED_USERS=<your-github-username>
EOF

# Push secrets to Modal and deploy
yaucca-deploy-secrets
modal deploy src/yaucca/cloud/modal_app.py

# Verify
curl https://<your-username>--yaucca-serve.modal.run/health
```

### Step 2: Use it everywhere

On every machine or cloud environment where you use Claude Code:

```bash
# Install the client (hooks only — lightweight)
uv pip install yaucca

# Install hooks into ~/.claude/settings.json
yaucca-install

# Add the remote MCP server
claude mcp add --transport http -s project yaucca \
  https://<your-username>--yaucca-serve.modal.run/mcp
```

**First-time MCP auth:** Claude Code will show `! Needs authentication`
for the yaucca server. To connect:

1. Type `/mcp` in the Claude Code prompt
2. Select yaucca → browser opens → GitHub login → authorize
3. "Authentication successful. Connected to yaucca."
4. All 7 memory tools are now available

The OAuth token is cached and refreshed automatically.

**Claude.ai web / mobile:** Go to Settings → Integrations → Add custom
integration → paste `https://<your-username>--yaucca-serve.modal.run/mcp`
→ walk through GitHub OAuth. No hooks on these surfaces, but all 7 MCP tools
are available.

**Claude Code cloud environments:** Add `uv pip install yaucca` to your
setup script and set `YAUCCA_URL` + `YAUCCA_AUTH_TOKEN` as environment
variables. Hooks fire automatically if configured in the repo's
`.claude/settings.json`.

### Hook lifecycle

- **SessionStart**: Injects memory context (core blocks + recent exchanges)
- **Stop** (every turn): Persists raw exchanges to archival — cheap HTTP POSTs, no LLM calls
- **SessionEnd** (on exit): Single `claude -p` call generates both an archival summary and an updated context block

### Rollback

```bash
# If hooks are broken, uninstall them
yaucca-install --uninstall

# If MCP is broken, remove it
claude mcp remove -s project yaucca

# Restore a backup of settings.json
cp ~/.claude/settings.json.bak ~/.claude/settings.json
```

The SQLite database on Modal's persistent volume is never modified by
rollback — your memory is always safe.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `YAUCCA_URL` | *(required)* | Your Modal deployment URL |
| `YAUCCA_AUTH_TOKEN` | *(none)* | Bearer token for the REST API (hooks use this) |
| `YAUCCA_REQUIRED` | `false` | If `true`, hooks exit non-zero when cloud is unreachable |

Server-side (set in Modal secrets):

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | *(required)* | For Qwen3-Embedding-8B embeddings |
| `YAUCCA_ISSUER_URL` | *(required)* | Public URL of your deployment (OAuth issuer) |
| `GITHUB_CLIENT_ID` | *(required)* | From your GitHub OAuth App |
| `GITHUB_CLIENT_SECRET` | *(required)* | From your GitHub OAuth App |
| `GITHUB_ALLOWED_USERS` | *(required)* | Comma-separated GitHub usernames allowed to authorize |

### Stateful Agent Mode

Set `YAUCCA_REQUIRED=true` when running yaucca as a critical dependency (e.g.
for a stateful agent that must have memory). In this mode, hooks will fail hard
(exit 1) if the cloud is unreachable, rather than silently starting without
memory.

## Embedding Model Comparison

yaucca supports multiple embedding profiles for A/B testing retrieval quality
across different models or Matryoshka dimension truncations.

Each embedding profile creates a separate `passages_vec_{name}` table in SQLite.
When a passage is inserted, its embedding is truncated to each profile's
dimension and stored in every active profile's table. Search can target a
specific profile via `?profile=` query parameter.

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

Backfill existing passages into new profiles via the admin endpoint:

```bash
curl -X POST "$YAUCCA_URL/api/admin/backfill?profile=d512" \
  -H "Authorization: Bearer $YAUCCA_AUTH_TOKEN"
```

## Development

```bash
git clone https://github.com/jakemannix/yaucca.git
cd yaucca
uv sync --extra dev                # Install all deps (client + server + test)
uv run pytest                      # Unit tests (114 tests)
uv run ruff check . && ruff format .  # Lint + format
uv run mypy src/yaucca             # Type check
```

## License

Apache-2.0
