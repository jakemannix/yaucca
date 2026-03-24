# yaucca — Development Instructions

## What This Is

yaucca (Yet Another Useless Claude Code Agent) gives Claude Code persistent long-term memory via a cloud-hosted SQLite + sqlite-vec backend on Modal.com. It uses Claude Code's hook system and MCP servers to create a stateful lifecycle:

1. **SessionStart hook** injects memory context from yaucca cloud
2. **MCP tools** let Claude read/update memory during sessions
3. **Stop hook** persists raw exchanges after each turn (cheap, no LLM)
4. **SessionEnd hook** generates a session summary + context block update (single `claude -p` call, runs only on exit)

## Architecture

- **Hooks** (`src/yaucca/hooks.py`): SessionStart + Stop + SessionEnd lifecycle scripts
- **Prompt** (`src/yaucca/prompt.py`): Memory rendering (XML blocks, metadata, recall)
- **Config** (`src/yaucca/config.py`): Pydantic settings for cloud connection
- **Cloud** (`src/yaucca/cloud/`): FastAPI server + remote MCP (OAuth 2.1), SQLite + sqlite-vec, Modal deployment

## Development

```bash
uv sync --extra dev               # Install all deps (client + server + test)
uv run pytest                     # Run unit tests
uv run pytest --cov               # With coverage
uv run ruff check .               # Lint
uv run ruff format .              # Format
uv run mypy src/yaucca            # Type check
```

## Key Design Decisions

- Remote MCP with OAuth 2.1 + GitHub authentication (7 tools)
- Cloud backend: SQLite + sqlite-vec on Modal.com (replaced Letta)
- Client install (`uv pip install yaucca`) = hooks only (httpx + pydantic-settings)
- Deploy install (`uv pip install yaucca[deploy]`) = server + Modal deployment
- Stop hook = Layer 1 only (raw turn persistence, no LLM calls)
- SessionEnd hook = Layers 2+3 (single `claude -p` for summary + context block)
- 5 memory blocks: user, projects, patterns, learnings, context
- Qwen3-Embedding-8B (1024 dims) via OpenRouter for semantic search

## Memory Block Semantics

| Block | Purpose |
|---|---|
| `user` | Who the user is, preferences, work style |
| `projects` | Active projects, repos, status |
| `patterns` | Code conventions, tools, recurring approaches |
| `learnings` | Debugging insights, lessons learned |
| `context` | Current session state, recent decisions |
