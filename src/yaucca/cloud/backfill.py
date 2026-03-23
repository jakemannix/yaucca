"""Backfill embedding profiles for existing passages.

Re-embeds passages that are missing from a target profile's vec table.
Used when adding a new embedding profile to an existing database, or when
switching to a different embedding model.

Can run as:
  - Server endpoint: POST /api/admin/backfill?profile=d512
  - CLI: uv run python -m yaucca.cloud.backfill --profile d512
"""

import asyncio
import logging
import sys

import httpx

from yaucca.cloud.db import Database
from yaucca.cloud.embed import Embedder

logger = logging.getLogger("yaucca.cloud.backfill")


async def backfill_profile(
    db: Database,
    embedder: Embedder,
    profile_name: str,
    batch_size: int = 50,
) -> dict[str, int]:
    """Backfill a single embedding profile for all passages missing from it.

    Embeds in batches for efficiency. Returns {"total": N, "backfilled": M, "errors": E}.
    """
    missing = db.passages_needing_backfill(profile_name)
    total = len(missing)
    backfilled = 0
    errors = 0

    logger.info("Backfill %s: %d passages to embed (batch_size=%d)", profile_name, total, batch_size)

    for i in range(0, total, batch_size):
        batch = missing[i : i + batch_size]
        try:
            texts = [p.text for p in batch]
            embeddings = await embedder.embed_batch(texts)
            for passage, embedding in zip(batch, embeddings):
                db.store_backfill_embedding(passage.id, embedding, profile_name)
            backfilled += len(batch)
            logger.info("  Progress: %d/%d", min(i + batch_size, total), total)
        except Exception as e:
            errors += len(batch)
            logger.warning("  Batch at offset %d failed (%d items): %s", i, len(batch), e)

    logger.info("Backfill %s complete: %d/%d embedded, %d errors", profile_name, backfilled, total, errors)
    return {"total": total, "backfilled": backfilled, "errors": errors}


async def backfill_all_profiles(
    db: Database,
    embedder: Embedder,
) -> dict[str, dict[str, int]]:
    """Backfill all active embedding profiles."""
    results = {}
    for profile in db.active_profiles:
        results[profile.name] = await backfill_profile(db, embedder, profile.name)
    return results


# --- CLI entry point ---


async def _cli_backfill(profile: str | None) -> None:
    """Run backfill against the cloud API.

    This fetches passages from the API and re-submits them, which triggers
    the server-side embedder. Useful when you can't access the DB directly.
    """
    from yaucca.config import get_settings

    settings = get_settings()
    headers: dict[str, str] = {}
    if settings.cloud.auth_token:
        headers["Authorization"] = f"Bearer {settings.cloud.auth_token}"

    async with httpx.AsyncClient(base_url=settings.cloud.url, headers=headers, timeout=30.0) as client:
        # Trigger server-side backfill
        params: dict[str, str] = {}
        if profile:
            params["profile"] = profile

        logger.info("Triggering backfill on %s ...", settings.cloud.url)
        resp = await client.post("/api/admin/backfill", params=params, timeout=300.0)
        resp.raise_for_status()
        result = resp.json()
        for name, stats in result.items():
            logger.info("  %s: %d/%d backfilled, %d errors", name, stats["backfilled"], stats["total"], stats["errors"])


def main() -> None:
    import argparse

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="backfill: %(message)s")

    parser = argparse.ArgumentParser(description="Backfill embedding profiles")
    parser.add_argument("--profile", help="Profile name to backfill (default: all profiles)")
    args = parser.parse_args()

    asyncio.run(_cli_backfill(args.profile))


if __name__ == "__main__":
    main()
