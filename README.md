# yaucca

**Yet Another Useless Claude Code Agent** — persistent long-term memory for Claude Code via [Letta](https://github.com/letta-ai/letta).

Every Claude Code session starts with full memory context and ends by persisting what happened. Memory survives across sessions, projects, and context compactions.

## How It Works

```
SessionStart hook → loads memory from Letta → injects into Claude's context
                           ↕
               MCP tools available during session
               (read/update blocks, search/store archival)
                           ↕
      Stop hook → extracts last exchange → persists to Letta archival
```

### Memory Tiers

1. **Core Memory** (5 blocks, always loaded): `user`, `projects`, `patterns`, `learnings`, `context`
2. **Archival Memory** (searchable): Long-term storage with semantic search
3. **Recall Memory** (pre-loaded): Recent conversation history injected at startup

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Docker (for local Letta server) or access to a Letta server

### Setup

```bash
# Clone
git clone https://github.com/jakemannix/yetanotheruseless_claude_code_agent.git
cd yetanotheruseless_claude_code_agent

# Install dependencies
uv sync

# Start Letta server (or use existing one)
docker compose up -d

# Create agent
uv run python -m yaucca.setup_agent
# → prints agent ID

# Configure
cp .env.example .env
# Set YAUCCA_AGENT_ID=<agent-id-from-above>

# Install globally for Claude Code
./scripts/install.sh
```

### Verify

```bash
# Check Letta is running
curl http://localhost:8283/v1/health

# Test SessionStart hook
echo '{"source":"startup"}' | uv run python -m yaucca.hooks session_start

# Open Claude Code — it should load your memory context
claude
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LETTA_BASE_URL` | `http://localhost:8283` | Letta server URL |
| `LETTA_API_KEY` | *(none)* | API key for authenticated Letta servers |
| `YAUCCA_AGENT_ID` | *(required)* | Letta agent ID for yaucca |

### Sharing Letta with Nameless

If you already run a Letta server for [Nameless](https://github.com/jakemannix/nameless), yaucca shares the same server with a separate agent. Just point `LETTA_BASE_URL` at the same instance.

## Development

```bash
uv run pytest                     # Unit tests
uv run pytest -k integration      # Integration tests (needs Letta)
uv run ruff check . && ruff format .  # Lint + format
uv run mypy src/yaucca            # Type check
```

## License

Apache-2.0
