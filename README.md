# yaucca

**Yet Another Useless Claude Code Agent** — persistent long-term memory for
[Claude Code](https://docs.anthropic.com/en/docs/claude-code), deployed as a
self-hosted [FastAPI](https://fastapi.tiangolo.com/) +
[SQLite](https://sqlite.org/) +
[sqlite-vec](https://github.com/asg017/sqlite-vec) backend on
[Modal.com](https://modal.com).

Inspired by the [MemGPT](https://arxiv.org/abs/2310.08560) tiered memory
architecture (now [Letta](https://www.letta.com/)), but built as a lightweight
single-container stack with scale-to-zero billing (~$0-1/month).

Every Claude Code session starts with full memory context and ends by
persisting what happened. Memory survives across sessions, projects, and
context compactions — accessible from Claude Code (laptop), Claude.ai (web),
and Claude mobile (phone).

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Modal.com (scale-to-zero, ~$0-1/month)              │
│                                                      │
│  FastAPI + SQLite + sqlite-vec + Qwen3 embeddings    │
│  Remote MCP (OAuth 2.1 + GitHub login)               │
│  Persistent volume: /data/yaucca.db                  │
└───────────────┬──────────────────────────────────────┘
                │ HTTPS
    ┌───────────┼───────────┐
    │           │           │
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

- **[Hooks](https://docs.anthropic.com/en/docs/claude-code/hooks)** (Claude
  Code only): SessionStart injects memory, Stop persists raw exchanges,
  SessionEnd generates a summary via `claude -p`
- **[Remote MCP](https://modelcontextprotocol.io/)** (all surfaces): 7 tools
  for reading/writing memory blocks, semantic search over archival passages,
  and progressive disclosure drill-down
- **[OAuth 2.1](https://www.rfc-editor.org/rfc/rfc6749)**: GitHub login gates
  access — only allowed users can connect

### Memory tiers

1. **Core Memory** (5 blocks, always loaded): `user`, `projects`, `patterns`,
   `learnings`, `context`
2. **Archival Memory** (searchable): Long-term storage with
   [Qwen3-Embedding-8B](https://huggingface.co/Qwen/Qwen3-Embedding-8B)
   semantic vector search (1024 dims via [OpenRouter](https://openrouter.ai))
3. **Recall Memory** (pre-loaded): Recent conversation history injected at
   startup

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [Modal](https://modal.com) account (free tier works)
- [OpenRouter](https://openrouter.ai) API key (for embeddings)
- [GitHub OAuth App](https://github.com/settings/developers) (for MCP auth)

### Step 1: Deploy your backend (once, from your laptop)

```bash
uv pip install yaucca[deploy]

# Authenticate with Modal
modal setup

# Create a GitHub OAuth App at https://github.com/settings/developers
#   Homepage URL: https://<your-modal-username>--yaucca-serve.modal.run
#   Callback URL: https://<your-modal-username>--yaucca-serve.modal.run/oauth/github/callback

# Generate an auth token
export YAUCCA_AUTH_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "Save this: YAUCCA_AUTH_TOKEN=$YAUCCA_AUTH_TOKEN"

# Create .env
cat > .env << EOF
YAUCCA_URL=https://<your-modal-username>--yaucca-serve.modal.run
YAUCCA_AUTH_TOKEN=$YAUCCA_AUTH_TOKEN
OPENROUTER_API_KEY=<your-openrouter-key>
YAUCCA_ISSUER_URL=https://<your-modal-username>--yaucca-serve.modal.run
GITHUB_CLIENT_ID=<from-github-oauth-app>
GITHUB_CLIENT_SECRET=<from-github-oauth-app>
GITHUB_ALLOWED_USERS=<your-github-username>
EOF

# Push secrets to Modal and deploy
yaucca-deploy-secrets
modal deploy src/yaucca/cloud/modal_app.py

# Verify
curl https://<your-modal-username>--yaucca-serve.modal.run/health
```

### Step 2: Use it everywhere

On every machine or cloud environment where you use Claude Code:

```bash
uv pip install yaucca

# Install hooks + memory rules template
yaucca-install

# Add the remote MCP server
claude mcp add --transport http -s user yaucca \
  https://<your-modal-username>--yaucca-serve.modal.run/mcp
```

`yaucca-install` does three things:

1. **Hooks** → added to `~/.claude/settings.json` (SessionStart, Stop,
   SessionEnd — see [Hook lifecycle](#hook-lifecycle) below)
2. **Memory rules** → installed at `~/.claude/rules/yaucca-memory.md` —
   teaches Claude how to use the memory blocks (read-modify-write, hygiene,
   when to update each block). Edit this file to customize.
3. **`.env` check** → warns if `YAUCCA_URL` / `YAUCCA_AUTH_TOKEN` aren't
   configured

First-time MCP auth:

1. Type `/mcp` in Claude Code
2. Select yaucca → browser opens → GitHub login → authorize
3. All 7 memory tools are now available (token auto-refreshes)

**Claude.ai web / mobile:** Settings → Integrations → Add custom
integration → paste `https://<your-modal-username>--yaucca-serve.modal.run/mcp`
→ GitHub OAuth. No hooks on these surfaces — use MCP tools directly, or
add instructions to your Claude.ai project to call them.

**Claude Code cloud environments:** Add to your setup script:

```bash
uv pip install yaucca && yaucca-install
```

Set `YAUCCA_URL` + `YAUCCA_AUTH_TOKEN` as environment variables in the
cloud environment config.

### First session — seeding memory

On your first session, memory blocks are empty. Claude will start populating
them as you work — the memory rules template guides it on what goes where.
You can also seed blocks manually via the MCP tools:

```
> Use the yaucca tools to update the "user" block with: "Name: ..."
> Update the "projects" block with my current active projects.
```

Or via the REST API:

```bash
curl -X PUT "$YAUCCA_URL/api/blocks/user" \
  -H "Authorization: Bearer $YAUCCA_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value": "Name: Your Name\nRole: ..."}'
```

### Hook lifecycle

| Hook | When | What | Cost |
|------|------|------|------|
| **SessionStart** | Session opens | Injects core blocks + recent exchanges | 1 HTTP GET |
| **Stop** | Every turn | Persists raw exchanges | 1 HTTP POST |
| **SessionEnd** | Session closes | `claude -p` generates summary + updates context block | 1 LLM call |

### Rollback

```bash
yaucca-install --uninstall              # remove hooks
claude mcp remove -s user yaucca        # remove MCP
cp ~/.claude/settings.json.bak ~/.claude/settings.json  # restore backup
```

Your data on Modal is never touched by rollback.

## Configuration

### Client-side (set in `.env` or environment)

| Variable | Default | Description |
|---|---|---|
| `YAUCCA_URL` | *(required)* | Your Modal deployment URL |
| `YAUCCA_AUTH_TOKEN` | *(none)* | Bearer token for the REST API (hooks use this) |
| `YAUCCA_REQUIRED` | `false` | If `true`, hooks fail hard when cloud is unreachable |

### Server-side (set in Modal secrets via `yaucca-deploy-secrets`)

| Variable | Description |
|---|---|
| `YAUCCA_AUTH_TOKEN` | Same token as client-side — authenticates hook REST calls |
| `OPENROUTER_API_KEY` | For [Qwen3-Embedding-8B](https://huggingface.co/Qwen/Qwen3-Embedding-8B) embeddings |
| `YAUCCA_ISSUER_URL` | Public URL of your deployment (OAuth issuer) |
| `GITHUB_CLIENT_ID` | From your [GitHub OAuth App](https://github.com/settings/developers) |
| `GITHUB_CLIENT_SECRET` | From your GitHub OAuth App |
| `GITHUB_ALLOWED_USERS` | Comma-separated GitHub usernames allowed to authorize |

## Embedding Profiles

yaucca supports multiple embedding profiles for A/B testing retrieval quality.
Each profile creates a separate `passages_vec_{name}` table. Search targets a
profile via `?profile=` query param.

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

Backfill existing passages into new profiles:

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

[Apache-2.0](LICENSE)
