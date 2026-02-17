"""Integration tests against a real Letta server.

These tests require a running Letta server at localhost:8283.
They create disposable agents to avoid polluting existing data.

Run with: uv run pytest -k integration -v
"""

import contextlib
import warnings

import pytest
from letta_client import Letta
from letta_client.types import CreateBlockParam

LETTA_URL = "http://localhost:8283"


def _letta_available() -> bool:
    """Check if Letta server is reachable."""
    try:
        client = Letta(base_url=LETTA_URL)
        client.agents.list()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.integration


@pytest.fixture
def letta_client() -> Letta:
    if not _letta_available():
        pytest.skip("Letta server not available at localhost:8283")
    return Letta(base_url=LETTA_URL)


@pytest.fixture
def disposable_agent(letta_client: Letta):
    """Create a disposable agent with 5 coding blocks for testing.

    Copies llm_config/embedding_config from an existing agent.
    Cleans up after test.
    """
    # Find reference agent for configs
    agents = letta_client.agents.list()
    items = agents.items if hasattr(agents, "items") else agents
    ref_agent = None
    for a in items:
        if hasattr(a, "llm_config") and a.llm_config:
            ref_agent = a
            break

    if not ref_agent:
        pytest.skip("No reference agent found on Letta server")

    blocks = [
        CreateBlockParam(label="user", value="Test user", description="Test", limit=5000),
        CreateBlockParam(label="projects", value="Test project", description="Test", limit=10000),
        CreateBlockParam(label="patterns", value="Test patterns", description="Test", limit=10000),
        CreateBlockParam(label="learnings", value="Test learnings", description="Test", limit=10000),
        CreateBlockParam(label="context", value="Test context", description="Test", limit=5000),
    ]

    agent = letta_client.agents.create(
        name="yaucca-test-disposable",
        memory_blocks=blocks,
        llm_config=ref_agent.llm_config,
        embedding_config=ref_agent.embedding_config,
    )

    # Create and attach archive
    archive = letta_client.archives.create(name="yaucca-test-disposable's Archive")
    letta_client.agents.archives.attach(archive.id, agent_id=agent.id)

    yield agent

    # Cleanup
    with contextlib.suppress(Exception):
        letta_client.agents.delete(agent.id)
    with contextlib.suppress(Exception):
        letta_client.archives.delete(archive.id)


class TestLettaIntegration:
    def test_list_blocks(self, letta_client: Letta, disposable_agent) -> None:
        blocks = letta_client.agents.blocks.list(disposable_agent.id)
        block_items = blocks.items if hasattr(blocks, "items") else blocks
        labels = {b.label for b in block_items}
        assert "user" in labels
        assert "projects" in labels

    def test_read_update_block(self, letta_client: Letta, disposable_agent) -> None:
        block = letta_client.agents.blocks.retrieve("user", agent_id=disposable_agent.id)
        assert block.value == "Test user"

        letta_client.agents.blocks.update("user", agent_id=disposable_agent.id, value="Updated user")
        block = letta_client.agents.blocks.retrieve("user", agent_id=disposable_agent.id)
        assert block.value == "Updated user"

    def test_archival_roundtrip(self, letta_client: Letta, disposable_agent) -> None:
        # Get archive_id
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            passages = letta_client.agents.passages.list(disposable_agent.id, limit=1)

        if passages and hasattr(passages[0], "archive_id"):
            archive_id = passages[0].archive_id
        else:
            # Find archive by name
            archives = letta_client.archives.list()
            items = archives.items if hasattr(archives, "items") else archives
            archive_id = None
            for a in items:
                if "yaucca-test-disposable" in (getattr(a, "name", "") or ""):
                    archive_id = a.id
                    break
            if not archive_id:
                pytest.skip("Could not resolve archive_id")

        # Insert
        letta_client.archives.passages.create(archive_id, text="Integration test memory")

        # Search
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            results = letta_client.agents.passages.list(
                disposable_agent.id,
                search="Integration test",
                limit=5,
            )
        texts = [r.text for r in results]
        assert any("Integration test" in t for t in texts)
