"""Create and configure a Letta agent for yaucca.

Sets up a new agent with 5 coding-focused memory blocks and an attached
archive for archival memory. Reuses llm_config/embedding_config from an
existing agent on the same Letta server (avoids hardcoding model configs).

Idempotent: skips creation if the agent already exists.

Usage:
    python -m yaucca.setup_agent [--name NAME] [--base-url URL]
"""

import argparse
import sys

from letta_client import Letta

from yaucca.config import get_settings

AGENT_NAME = "yaucca"

# Default block definitions for a coding-focused agent
DEFAULT_BLOCKS = {
    "user": {
        "value": "The user of this Claude Code session. Update this as you learn about them.",
        "description": "Information about the user — preferences, projects, work style",
        "limit": 5000,
    },
    "projects": {
        "value": "Active projects and their current status. Update as work progresses.",
        "description": "Active projects, repos, and goals being worked on",
        "limit": 10000,
    },
    "patterns": {
        "value": "Code patterns, conventions, and tools observed across sessions.",
        "description": "Recurring patterns, conventions, preferred tools and approaches",
        "limit": 10000,
    },
    "learnings": {
        "value": "Insights and lessons learned from past sessions.",
        "description": "Hard-won insights, debugging lessons, things that worked or didn't",
        "limit": 10000,
    },
    "context": {
        "value": "Current working context. Updated each session.",
        "description": "Current session context — what we're working on, recent decisions",
        "limit": 5000,
    },
}


def _find_reference_agent(client: Letta) -> dict | None:
    """Find an existing agent to copy llm_config/embedding_config from."""
    try:
        agents = client.agents.list()
        items = agents.items if hasattr(agents, "items") else agents
        for agent in items:
            if hasattr(agent, "llm_config") and agent.llm_config:
                return {
                    "llm_config": agent.llm_config,
                    "embedding_config": agent.embedding_config,
                }
    except Exception:
        pass
    return None


def setup_agent(name: str = AGENT_NAME, base_url: str | None = None) -> str:
    """Create a yaucca agent on the Letta server.

    Returns the agent ID (prints to stdout for scripting).
    """
    settings = get_settings()
    url = base_url or settings.letta.base_url

    kwargs: dict = {"base_url": url}
    if settings.letta.api_key:
        kwargs["token"] = settings.letta.api_key
    client = Letta(**kwargs)

    # Check if agent already exists
    agents = client.agents.list()
    items = agents.items if hasattr(agents, "items") else agents
    for agent in items:
        if getattr(agent, "name", "") == name:
            print(f"Agent '{name}' already exists: {agent.id}", file=sys.stderr)
            print(agent.id)
            return agent.id

    # Get reference configs from existing agent
    ref = _find_reference_agent(client)
    if not ref:
        print("ERROR: No existing agent found to copy llm_config from.", file=sys.stderr)
        print("Create at least one agent on this Letta server first.", file=sys.stderr)
        sys.exit(1)

    # Build block configs (CreateBlockParam is a TypedDict in letta-client 1.7+)
    from letta_client.types import CreateBlockParam

    blocks = [
        CreateBlockParam(
            label=label,
            value=config["value"],
            description=config["description"],
            limit=config["limit"],
        )
        for label, config in DEFAULT_BLOCKS.items()
    ]

    # Create the agent
    agent = client.agents.create(
        name=name,
        memory_blocks=blocks,
        llm_config=ref["llm_config"],
        embedding_config=ref["embedding_config"],
    )

    print(f"Created agent '{name}': {agent.id}", file=sys.stderr)

    # Create and attach archive for archival memory
    try:
        archive = client.archives.create(name=f"{name}'s Archive")
        client.agents.archives.attach(archive.id, agent_id=agent.id)
        print(f"Created and attached archive: {archive.id}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: could not create archive: {e}", file=sys.stderr)

    # Print agent ID to stdout for scripting
    print(agent.id)
    return agent.id


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Create a yaucca agent on Letta")
    parser.add_argument("--name", default=AGENT_NAME, help="Agent name (default: yaucca)")
    parser.add_argument("--base-url", default=None, help="Letta server URL")
    args = parser.parse_args()

    setup_agent(name=args.name, base_url=args.base_url)


if __name__ == "__main__":
    main()
