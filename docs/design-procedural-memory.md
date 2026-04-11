# Design: Procedural Memory — Agent-Authored Virtual Tool Registry

**Priority**: High (foundational architecture)
**Status**: Proposed
**Related**: virtual-tools-spec, agentgateway, yaucca, MCPGraph

## Core Insight

yaucca gives Claude **episodic memory** (conversation history, session summaries)
and **semantic memory** (core blocks, archival knowledge, GTD state). But there's
a third kind of memory missing: **procedural memory** — how to do things.

Today, procedures live as code in repos or natural language in prompts. Neither
is ideal:
- Code requires a developer to write and deploy
- Natural language is ambiguous, not composable, not shareable

**Procedural memory should be structured, mutable, registerable virtual tool
definitions** that the agent can author, evolve, and share — in JSON, not code.

## Why JSON, Not Code

1. **Non-technical readability** — a product manager can read, understand, and
   edit a virtual tool definition
2. **Portability** — any MCP-enabled agent can use it (Claude, GPT, Gemini,
   local models). Not locked to one runtime.
3. **Security & observability** — the *dataplane* that executes virtual tools
   handles auth, rate limiting, tracing, cost tracking. The *definition* is
   inert data — it can't escape its sandbox.
4. **Ecosystem** — open spec means dataplanes can compete on execution quality
   (agentgateway, MCPGraph, custom implementations) while sharing a common
   definition format.

## Three-Plane Architecture

```
┌─────────────────────────────────────────────────┐
│  Control Plane (Registry)                        │
│                                                  │
│  - Virtual tool definitions (JSON)               │
│  - Agent / skill / workflow metadata             │
│  - MCP server for CRUD + discovery               │
│  - Versioned, auditable, diffable                │
│  - Auth: who can read/write/execute each tool    │
│                                                  │
│  Implementation: MCP server on Modal             │
│  Storage: SQLite (like yaucca) or git-backed     │
│  Format: virtual-tools-spec (or evolution of)    │
├─────────────────────────────────────────────────┤
│  Data Plane (Execution)                          │
│                                                  │
│  - Resolves virtual tools → base tool calls      │
│  - Handles combinators:                          │
│    scatter-gather, pipeline, conditional,        │
│    dedupe, retry, fallback                       │
│  - Observability: OpenTelemetry tracing,         │
│    metrics, cost tracking per tool call          │
│  - Security: auth propagation, rate limits,      │
│    input validation, output sanitization         │
│  - Sandboxing: tools can't access each other's   │
│    state or escalate privileges                  │
│                                                  │
│  Implementations (compete on quality):           │
│    - agentgateway (Rust, production-grade)        │
│    - MCPGraph (by MCPInspector author)            │
│    - Custom Python/Node (lightweight)             │
├─────────────────────────────────────────────────┤
│  Memory Plane (yaucca)                           │
│                                                  │
│  - Episodic memory (exchanges, summaries)        │
│  - Semantic memory (core blocks, archival)       │
│  - GTD / task state                              │
│  - Procedural memory refs → registry entries     │
│    (yaucca knows *that* a tool exists and when   │
│    it was last used; registry knows *how* it     │
│    works)                                        │
└─────────────────────────────────────────────────┘
```

## Agent SDLC for Procedural Memory

The agent follows a simplified software development lifecycle when
creating and maintaining virtual tools:

### 1. Identify
Agent notices a recurring multi-step pattern in its work.
> "I keep searching 4 engines, deduping by URL, then fetching content."

### 2. Author
Agent writes a virtual tool definition using a structured schema:

```json
{
  "name": "multi_source_research",
  "description": "Search multiple engines, dedupe results, fetch full content",
  "type": "pipeline",
  "steps": [
    {
      "name": "search",
      "type": "scatter-gather",
      "fan_out": [
        {"tool": "exa_search", "field_map": {"query": "q"}},
        {"tool": "tavily_search"},
        {"tool": "arxiv_search", "field_map": {"query": "search_query"}}
      ],
      "combine": {
        "strategy": "dedupe",
        "key": "url",
        "merge": "prefer_longest_description"
      }
    },
    {
      "name": "fetch",
      "type": "map",
      "tool": "fetch_url",
      "input": "$.search.results[*].url",
      "concurrency": 5
    },
    {
      "name": "summarize",
      "type": "single",
      "tool": "llm_summarize",
      "input": "$.fetch.results"
    }
  ]
}
```

### 3. Register
Agent pushes the definition to the registry via MCP tool call:

```
create_virtual_tool(definition={...})
```

The registry validates the schema, checks that referenced base tools
exist, assigns a version, and makes it discoverable.

### 4. Use
Next time the pattern occurs, agent calls the composed tool:

```
multi_source_research(query="transformer attention mechanisms")
```

The dataplane resolves the definition, executes the pipeline, handles
retries/timeouts, and returns the result.

### 5. Refine
Agent updates the definition when requirements change:

```
update_virtual_tool(name="multi_source_research", changes={
  "steps[0].fan_out": [... add google_scholar ...]
})
```

Registry creates a new version. Old version remains available for
rollback.

### 6. Share
Other agents or users can discover and use the tool:

```
search_registry(query="research tools")
→ multi_source_research v3 (by jake, used 47 times)
```

Cross-agent sharing works because the format is MCP-native — any
MCP-enabled agent can call it.

## The Authoring Tool

The registry exposes MCP tools for agent self-authoring:

| Tool | Purpose |
|------|---------|
| `create_virtual_tool(definition)` | Register a new virtual tool |
| `update_virtual_tool(name, changes)` | Create a new version |
| `list_virtual_tools(filter?)` | Browse the registry |
| `get_virtual_tool(name, version?)` | Read a definition |
| `delete_virtual_tool(name)` | Remove (soft delete) |
| `test_virtual_tool(name, input)` | Dry-run with sample input |
| `get_tool_usage(name)` | Execution stats, error rates |

## Registry Format

Build on virtual-tools-spec, extending it with:

- **Versioning**: semver on each tool definition
- **Provenance**: who created it (agent vs human), when, why
- **Usage stats**: call count, error rate, avg latency
- **Dependencies**: which base tools does this virtual tool require
- **Permissions**: who can read/write/execute
- **Tags/categories**: for discovery

## Deployment: Private GitOps Repo

Following the pattern from `design-gitops-deployment.md`:

```
my-agent-registry/              (private repo)
├── .github/workflows/
│   └── deploy.yml              # Auto-deploy registry to Modal
├── registry/
│   └── tools/                  # Virtual tool definitions (JSON)
│       ├── multi_source_research.json
│       ├── book_travel.json
│       └── daily_triage.json
├── config/
│   ├── modal-app.py            # Registry MCP server
│   └── dataplane.yaml          # Which dataplane to use
└── README.md
```

Push to main → GitHub Actions → Modal deploy → registry is live.

Tools can also be authored at runtime via MCP (agent writes them),
then optionally committed back to git for version control.

## Repo Structure

This spans multiple repos:

| Repo | Role | Status |
|------|------|--------|
| `virtual-tools-spec` | Registry format spec + reference editor | Existing, needs versioning/provenance extensions |
| `yaucca` | Memory plane (episodic + semantic + GTD) | Existing, add procedural memory refs |
| `agentgateway` / `MCPGraph` | Data plane (execution) | Existing, evaluate fit |
| **NEW**: `agent-registry` | Control plane MCP server | To be created |
| **NEW**: `agent-registry-infra` (private) | GitOps deployment per user | Scaffolded from template |

## Relationship to Existing Work

### virtual-tools-spec
The registry format. Extend with versioning, provenance, usage stats.
The editor UI becomes the "registry browser" for humans.

### agentgateway
One possible dataplane. Already does MCP proxying, virtual tool
resolution, scatter-gather. Evaluate whether it can consume registry
definitions directly or needs an adapter.

### MCPGraph
Alternative dataplane, particularly interesting for its graph-based
tool composition model. Could complement agentgateway rather than
replace it.

### yaucca
Stays as the memory substrate. Adds awareness of procedural memory:
- `projects` block tracks which virtual tools exist
- Archival memory stores tool authoring decisions
- GTD system can reference virtual tools as "how to do X"

## Open Questions

1. **Git-backed vs DB-backed registry?** Git gives version control
   for free but is slower for runtime reads. DB (SQLite) is fast but
   needs its own versioning. Hybrid: DB for runtime, git sync for
   audit trail?

2. **How does the agent decide when to author vs. just do?** Need
   heuristics: "if I've done this pattern 3+ times" or "if the user
   asks me to remember how to do something."

3. **Dataplane selection**: agentgateway is Rust (fast, production-grade
   but harder to extend). MCPGraph is newer. Custom Python is easiest
   to prototype. Start with Python, graduate to agentgateway?

4. **Multi-tenancy**: if the registry is shared, how do we handle
   tool namespacing? `jake/multi_source_research` vs
   `org/multi_source_research`?

5. **Testing**: how does an agent test a virtual tool before registering
   it? Dry-run mode with mocked base tools? Sandbox dataplane?

## Implementation Plan

### Phase 1: Registry MCP Server (new repo: `agent-registry`)
- SQLite-backed registry with CRUD operations
- MCP tools for create/read/update/delete/search
- Virtual-tools-spec JSON as the definition format
- OAuth (reuse yaucca's GitHub OAuth pattern)
- Deploy on Modal alongside yaucca

### Phase 2: Agent Authoring Skill
- Teach Claude Code to recognize recurring patterns
- Skill/prompt that guides virtual tool authoring
- Integration with yaucca (store authoring decisions)
- "Suggest a virtual tool" vs "auto-create" modes

### Phase 3: Dataplane Integration
- Connect registry to agentgateway or MCPGraph
- Agent calls virtual tools through the dataplane
- o11y: trace each virtual tool execution
- Evaluate dataplane options, pick one (or abstract)

### Phase 4: Ecosystem
- Public registry (npm-like) for shared virtual tools
- Community contributions
- Dataplane competition (multiple implementations)
- Non-technical editing UI (extend virtual-tools-spec editor)
