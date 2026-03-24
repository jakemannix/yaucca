# yaucca Memory System

You have persistent long-term memory powered by yaucca (cloud SQLite + sqlite-vec on Modal). Your memory survives across sessions, projects, and context compactions.

## Three-Tier Memory Model

1. **Core Memory (always loaded)** — Your memory blocks are injected into context at session start and after compaction. You can read and update them anytime with MCP tools.

2. **Archival Memory (searchable)** — Long-term storage for experiences, learnings, and insights. Search with `search_archival_memory`, store with `insert_archival_memory`. Entries are embedded for semantic search.

3. **Recall Memory (pre-loaded)** — Recent conversation history is automatically injected into your context. You do NOT need to call `get_recent_messages` — it's already there. Each conversation is automatically persisted after it ends.

## Core Memory Blocks

| Block | Purpose | When to Update |
|---|---|---|
| `user` | Who the user is, their preferences, work style | When you learn something new about the user |
| `projects` | Active projects, repos, goals, status | When projects start, change status, or complete |
| `patterns` | Code conventions, preferred tools, recurring approaches | When you notice a stable pattern across sessions |
| `learnings` | Debugging insights, things that worked or didn't | When you solve a hard problem or learn a lesson |
| `context` | Current session context, recent decisions | Each session to track what you're working on |

## Important: Read-Modify-Write

`update_memory_block` **replaces** the entire block value. Always:
1. Read the current value with `get_memory_block`
2. Modify the content
3. Write the full updated value back

## Memory Hygiene

- Keep blocks concise and structured — they have character limits
- Move detailed content to archival memory, keep summaries in core blocks
- Update `context` block early in each session
- Don't duplicate information across blocks
- Prune outdated entries when blocks get full
