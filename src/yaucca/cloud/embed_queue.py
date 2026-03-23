"""Async background queue for batched embedding and volume commits.

Passages are written to SQLite immediately (text only), then enqueued
for async embedding. A background worker flushes the queue when either:
  - batch reaches max_batch_size, OR
  - oldest item in queue exceeds max_wait_seconds

On flush: embed_batch → store all embeddings → volume.commit() once.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from yaucca.cloud.db import Database
from yaucca.cloud.embed import Embedder

logger = logging.getLogger("yaucca.cloud.embed_queue")


class EmbeddingQueue:
    """Batches embedding work and volume commits in the background.

    Args:
        db: Database instance (already connected).
        embedder: Embedder instance for generating vectors.
        commit_fn: Optional callback after writes (e.g., volume.commit).
        max_batch_size: Flush when queue reaches this size.
        max_wait_seconds: Flush when oldest item exceeds this age.
    """

    def __init__(
        self,
        db: Database,
        embedder: Embedder,
        commit_fn: Callable[[], None] | None = None,
        max_batch_size: int = 50,
        max_wait_seconds: float = 5.0,
    ) -> None:
        self._db = db
        self._embedder = embedder
        self._commit_fn = commit_fn
        self._max_batch = max_batch_size
        self._max_wait = max_wait_seconds
        self._queue: list[tuple[str, str, float]] = []  # (passage_id, text, enqueue_time)
        self._lock = asyncio.Lock()
        self._notify = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background worker."""
        self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Flush remaining items and stop the worker."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final flush
        batch: list[tuple[str, str, float]] = []
        async with self._lock:
            if self._queue:
                batch = self._queue[:]
                self._queue.clear()
        if batch:
            await self._flush(batch)

    async def enqueue(self, passage_id: str, text: str) -> None:
        """Add a passage for background embedding."""
        async with self._lock:
            self._queue.append((passage_id, text, time.monotonic()))
            qlen = len(self._queue)
        # Wake worker — if batch is full it'll flush immediately
        if qlen >= self._max_batch:
            self._notify.set()
        elif qlen == 1:
            # First item — wake worker to start the timeout
            self._notify.set()

    @property
    def pending(self) -> int:
        """Number of passages waiting for embedding."""
        return len(self._queue)

    async def _worker(self) -> None:
        """Background loop: wait for items, flush when batch full or timeout."""
        while True:
            # Wait for something to be enqueued
            await self._notify.wait()
            self._notify.clear()

            # Wait for batch to fill or timeout
            while True:
                async with self._lock:
                    qlen = len(self._queue)
                    oldest = self._queue[0][2] if self._queue else time.monotonic()

                if qlen >= self._max_batch:
                    break

                age = time.monotonic() - oldest
                remaining = self._max_wait - age
                if remaining <= 0:
                    break

                # Wait for more items or timeout
                try:
                    await asyncio.wait_for(self._wait_for_notify(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

            # Drain the queue
            async with self._lock:
                batch = self._queue[:]
                self._queue.clear()
                self._notify.clear()

            if batch:
                await self._flush(batch)

    async def _wait_for_notify(self) -> None:
        """Helper: wait for the notify event, then clear it."""
        await self._notify.wait()
        self._notify.clear()

    async def _flush(self, batch: list[tuple[str, str, float]]) -> None:
        """Embed a batch, store vectors, and commit."""
        ids = [item[0] for item in batch]
        texts = [item[1] for item in batch]

        try:
            t0 = time.monotonic()
            embeddings = await self._embedder.embed_batch(texts)
            t_embed = time.monotonic()

            for passage_id, embedding in zip(ids, embeddings):
                for profile in self._db.active_profiles:
                    truncated = embedding[: profile.dimensions]
                    self._db._store_embedding(passage_id, truncated, profile.table_name)
            self._db.conn.commit()

            if self._commit_fn:
                self._commit_fn()

            t_done = time.monotonic()
            logger.info(
                "Flushed %d embeddings: embed=%dms store+commit=%dms",
                len(batch),
                round((t_embed - t0) * 1000),
                round((t_done - t_embed) * 1000),
            )
        except Exception as e:
            logger.error("Embedding batch failed (%d items): %s — re-enqueueing", len(batch), e)
            # Re-enqueue failed items so they aren't silently lost
            async with self._lock:
                self._queue.extend(batch)
                self._notify.set()
