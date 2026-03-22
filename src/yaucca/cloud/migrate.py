"""One-time migration from Letta to yaucca cloud SQLite.

Reads all blocks and passages from the Letta API and inserts them into the
yaucca cloud database via its HTTP API.

Usage:
    YAUCCA_URL=https://yaucca--serve.modal.run \
    YAUCCA_AUTH_TOKEN=<token> \
    uv run python -m yaucca.cloud.migrate
"""

import json
import logging
import sys
import warnings

import httpx

from yaucca.config import get_settings

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="migrate: %(message)s")
logger = logging.getLogger("yaucca.migrate")


def migrate() -> None:
    settings = get_settings()
    agent_id = settings.agent.agent_id
    if not agent_id:
        logger.error("YAUCCA_AGENT_ID not set")
        sys.exit(1)

    cloud_url = settings.cloud.url
    cloud_token = settings.cloud.auth_token
    if not cloud_url:
        logger.error("YAUCCA_URL not set")
        sys.exit(1)

    headers: dict[str, str] = {}
    if cloud_token:
        headers["Authorization"] = f"Bearer {cloud_token}"

    # Connect to Letta
    from letta_client import Letta

    kwargs: dict[str, object] = {"base_url": settings.letta.base_url}
    if settings.letta.api_key:
        kwargs["token"] = settings.letta.api_key
    client = Letta(**kwargs)

    cloud = httpx.Client(base_url=cloud_url, headers=headers, timeout=30.0)

    # Migrate blocks
    logger.info("Migrating blocks...")
    blocks_page = client.agents.blocks.list(agent_id)
    blocks = blocks_page.items if hasattr(blocks_page, "items") else blocks_page
    for block in blocks:
        label = block.label
        value = block.value or ""
        resp = cloud.put(f"/api/blocks/{label}", json={"value": value})
        if resp.status_code == 200:
            logger.info("  Block '%s': %d chars", label, len(value))
        elif resp.status_code == 404:
            logger.warning("  Block '%s' not in cloud schema, skipping", label)
        else:
            logger.error("  Block '%s' failed: %s", label, resp.text)

    # Migrate passages
    logger.info("Migrating passages...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        passages = client.agents.passages.list(agent_id, limit=1000, ascending=True)

    count = 0
    for p in passages:
        text = getattr(p, "text", "") or ""
        tags = getattr(p, "tags", []) or []
        metadata = getattr(p, "metadata", {}) or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        resp = cloud.post(
            "/api/passages",
            json={"text": text, "tags": tags, "metadata": metadata},
        )
        if resp.status_code == 201:
            count += 1
        else:
            logger.error("  Passage failed: %s", resp.text[:200])

    logger.info("Migrated %d passages", count)
    logger.info("Done! Verify with: curl %s/api/blocks", cloud_url)


if __name__ == "__main__":
    migrate()
