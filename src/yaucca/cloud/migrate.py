"""One-time migration from Letta to yaucca cloud SQLite.

Reads all blocks and passages from the Letta API and inserts them into the
yaucca cloud database via its HTTP API.

Deduplicates passages by text content — safe to re-run after a partial failure.

Usage:
    YAUCCA_URL=https://yaucca--serve.modal.run \
    YAUCCA_AUTH_TOKEN=<token> \
    uv run python -m yaucca.cloud.migrate
"""

import json
import logging
import sys
import time
import warnings

import httpx

from yaucca.config import get_settings

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="migrate: %(message)s")
logger = logging.getLogger("yaucca.migrate")

# Longer timeout — each passage triggers a server-side embedding call
_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
_MAX_RETRIES = 3
_RETRY_DELAY = 5.0


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

    cloud = httpx.Client(base_url=cloud_url, headers=headers, timeout=_TIMEOUT)

    # Migrate blocks (idempotent — PUT overwrites)
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

    # Fetch existing passage texts from cloud for dedup
    logger.info("Fetching existing passages for dedup...")
    existing_resp = cloud.get("/api/passages", params={"limit": 10000})
    existing_resp.raise_for_status()
    existing_texts = {p["text"] for p in existing_resp.json()}
    logger.info("  %d passages already in cloud", len(existing_texts))

    # Migrate passages (skips duplicates by text content)
    logger.info("Migrating passages...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        passages = client.agents.passages.list(agent_id, limit=1000, ascending=True)

    count = 0
    skipped = 0
    errors = 0
    for i, p in enumerate(passages):
        text = getattr(p, "text", "") or ""
        if text in existing_texts:
            skipped += 1
            continue

        tags = getattr(p, "tags", []) or []
        metadata = getattr(p, "metadata", {}) or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        for attempt in range(_MAX_RETRIES):
            try:
                resp = cloud.post(
                    "/api/passages",
                    json={"text": text, "tags": tags, "metadata": metadata},
                )
                if resp.status_code == 201:
                    count += 1
                    existing_texts.add(text)
                    break
                else:
                    logger.error("  Passage %d failed (HTTP %d): %s", i, resp.status_code, resp.text[:200])
                    errors += 1
                    break
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt < _MAX_RETRIES - 1:
                    logger.warning("  Passage %d attempt %d failed: %s — retrying in %ds", i, attempt + 1, e, _RETRY_DELAY)
                    time.sleep(_RETRY_DELAY)
                else:
                    logger.error("  Passage %d failed after %d attempts: %s", i, _MAX_RETRIES, e)
                    errors += 1

        if (count + skipped) % 50 == 0 and count > 0:
            logger.info("  Progress: %d migrated, %d skipped, %d errors (of %d total)", count, skipped, errors, len(passages))

    logger.info("Done: %d migrated, %d skipped, %d errors (of %d total)", count, skipped, errors, len(passages))
    logger.info("Verify with: curl %s/api/blocks", cloud_url)


if __name__ == "__main__":
    migrate()
