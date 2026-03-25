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

# Guided setup: checks Modal, shows GitHub OAuth instructions,
# creates ~/.config/yaucca/.env, deploys to Modal
yaucca-deploy
```

`yaucca-deploy` walks you through each step:

1. **Modal account** — checks you're logged in (run `modal setup` if not)
2. **Server URL** — computed from your Modal username
3. **GitHub OAuth App** — tells you exactly what to fill in at
   https://github.com/settings/developers (Homepage URL, Callback URL)
4. **Configuration** — creates `~/.config/yaucca/.env` with your auth token
   pre-generated and placeholders for the keys you need to paste in
5. **Deploy** — pushes secrets to Modal and deploys (only after `.env` is complete)

First run will pause at step 4 and ask you to edit `~/.config/yaucca/.env`
with your OpenRouter API key and GitHub OAuth credentials. Fill those in,
then re-run `yaucca-deploy` to finish.

### Step 2: Use it everywhere

On every machine or cloud environment where you use Claude Code:

```bash
uv pip install yaucca

# Interactive setup: seeds your user profile, installs hooks + memory
# rules, adds the remote MCP server
yaucca-install
```

`yaucca-install` does four things:

1. **User profile** → interactively asks your name, role, etc. and seeds
   the `user` memory block on the server (skips if already seeded; use
   `--user-block "..."` to skip the interactive prompt)
2. **Hooks** → added to `~/.claude/settings.json` (SessionStart, Stop,
   SessionEnd — see [Hook lifecycle](#hook-lifecycle) below)
3. **Memory rules** → installed at `~/.claude/rules/yaucca-memory.md` —
   teaches Claude how to use the memory blocks (read-modify-write, hygiene,
   when to update each block). Edit this file to customize.
4. **MCP server** → runs `claude mcp add` to register the remote MCP server

**First-time MCP auth:** after install, start Claude Code and type `/mcp`
→ select yaucca → browser opens for GitHub login → authorize → connected.
Token auto-refreshes after that.

**Claude.ai web / mobile:** Settings → Integrations → Add custom
integration → paste your server URL `/mcp` → GitHub OAuth.

**Claude Code cloud environments:** Set `YAUCCA_URL` + `YAUCCA_AUTH_TOKEN`
as environment variables, then add to your setup script:

```bash
uv pip install yaucca && yaucca-install
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
