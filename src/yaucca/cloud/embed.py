"""Embedding generation for yaucca archival passages.

Supports OpenAI API embeddings and a stub for testing.
"""

from typing import Protocol

import httpx


class Embedder(Protocol):
    """Protocol for embedding text into vectors."""

    async def embed(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    """Generate embeddings using OpenAI's text-embedding-3-small model."""

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._client = httpx.AsyncClient(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    async def embed(self, text: str) -> list[float]:
        response = await self._client.post(
            "/embeddings",
            json={
                "input": text,
                "model": self._model,
                "dimensions": self._dimensions,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]


class StubEmbedder:
    """Returns zero vectors — for testing and development without an API key."""

    def __init__(self, dimensions: int = 1536) -> None:
        self._dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        return [0.0] * self._dimensions
