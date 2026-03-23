"""Embedding generation for yaucca archival passages.

Supports any OpenAI-compatible embeddings API (OpenRouter, OpenAI, etc.)
and a stub for testing. Default: Qwen3-Embedding-8B via OpenRouter.
"""

from typing import Protocol

import httpx


class Embedder(Protocol):
    """Protocol for embedding text into vectors."""

    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dimensions(self) -> int: ...


class OpenAICompatibleEmbedder:
    """Generate embeddings using any OpenAI-compatible API.

    Default: Qwen3-Embedding-8B via OpenRouter (1024 dims, Matryoshka).
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "qwen/qwen3-embedding-8b",
        dimensions: int = 1024,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.post(
            "/embeddings",
            json={
                "input": texts,
                "model": self._model,
                "dimensions": self._dimensions,
            },
        )
        response.raise_for_status()
        data = response.json()
        # API returns embeddings sorted by index
        return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]


class StubEmbedder:
    """Returns zero vectors — for testing and development without an API key."""

    def __init__(self, dimensions: int = 1024) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        return [0.0] * self._dimensions

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dimensions for _ in texts]
