# yaucca — Development Instructions

## What This Is

yaucca (Yet Another Useless Claude Code Agent) gives Claude Code persistent long-term memory via Letta. It uses Claude Code's hook system and MCP servers to create a stateful lifecycle:

1. **SessionStart hook** injects memory context from Letta
2. **MCP tools** let Claude read/update memory during sessions
3. **Stop hook** persists conversation summaries to Letta archival

## Architecture

- **MCP Server** (`src/yaucca/mcp_server.py`): FastMCP stdio server with 6 Letta tools
- **Hooks** (`src/yaucca/hooks.py`): SessionStart + Stop lifecycle scripts
- **Prompt** (`src/yaucca/prompt.py`): Memory rendering (XML blocks, metadata, recall)
- **Config** (`src/yaucca/config.py`): Pydantic settings for Letta connection
- **Setup** (`src/yaucca/setup_agent.py`): Create Letta agent with coding blocks

## Development

```bash
uv sync                           # Install deps
uv run pytest                     # Run unit tests
uv run pytest --cov               # With coverage
uv run pytest -k integration      # Integration tests (needs Letta)
uv run ruff check .               # Lint
uv run ruff format .              # Format
uv run mypy src/yaucca            # Type check
```

## Key Design Decisions

- FastMCP (not Claude Agent SDK) — native stdio MCP server for Claude Code
- Sync Letta client in hooks (short-lived), async in MCP server (long-lived)
- Shared Letta server with Nameless, separate agent ID
- 5 coding-focused memory blocks: user, projects, patterns, learnings, context
- `archives.passages.create` for archival writes (bypasses Letta LLM loop)

## Memory Block Semantics

| Block | Purpose |
|---|---|
| `user` | Who the user is, preferences, work style |
| `projects` | Active projects, repos, status |
| `patterns` | Code conventions, tools, recurring approaches |
| `learnings` | Debugging insights, lessons learned |
| `context` | Current session state, recent decisions |
