"""
Embedding Adapters for Vector Generation.
Supports both external LLM embedding APIs (OpenAI/LiteLLM) and local fallback.
"""
from __future__ import annotations
import math
import hashlib
from typing import Sequence
import httpx
from app.domain.ports import EmbeddingPort


class DeterministicHashEmbeddingAdapter(EmbeddingPort):
    """
    Zero-external-dependency, deterministic 1536-dimensional embedding generator.
    Used for local testing, offline development, and unit test suites.
    Produces unit-length (normalized) vectors based on n-gram feature hashing.
    """
    def __init__(self, dimension: int = 1536):
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    async def generate_embedding(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        words = text.lower().split()
        if not words:
            vec[0] = 1.0
            return vec

        # N-gram hashing into vector dimensions
        for i, word in enumerate(words):
            # Unigram hash
            h1 = int(hashlib.md5(word.encode()).hexdigest(), 16) % self._dim
            vec[h1] += 1.0
            # Bigram hash
            if i > 0:
                bigram = f"{words[i-1]}_{word}"
                h2 = int(hashlib.sha256(bigram.encode()).hexdigest(), 16) % self._dim
                vec[h2] += 1.5

        # L2 Normalization (ensuring vector length ||v|| = 1.0)
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        else:
            vec[0] = 1.0
        return vec


class OpenAIEmbeddingAdapter(EmbeddingPort):
    """
    Production embedding adapter calling OpenAI / LiteLLM API.
    """
    def __init__(self, api_key: str, model: str = "text-embedding-3-small", dimension: int = 1536):
        self._api_key = api_key
        self._model = model
        self._dim = dimension
        self._client = httpx.AsyncClient(timeout=10.0)

    @property
    def dimension(self) -> int:
        return self._dim

    async def generate_embedding(self, text: str) -> list[float]:
        response = await self._client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"input": text, "model": self._model},
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]
