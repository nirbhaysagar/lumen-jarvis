"""
Embedding Adapters for Vector Generation.
Supports local Ollama embedding API and deterministic hash fallback.
"""
from __future__ import annotations
import math
import hashlib
import re
from typing import Sequence
import httpx
from app.domain.ports import EmbeddingPort


class DeterministicHashEmbeddingAdapter(EmbeddingPort):
    """
    Zero-external-dependency, deterministic 768-dimensional embedding generator.
    Used for local testing, offline development, and unit test suites.
    Produces unit-length (normalized) vectors based on n-gram feature hashing.
    """
    def __init__(self, dimension: int = 768):
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    async def generate_embedding(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        words = re.findall(r"\w+", text.lower())
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


class OllamaEmbeddingAdapter(EmbeddingPort):
    """
    Local embedding adapter calling Ollama embedding API (e.g. nomic-embed-text, 768 dimensions).
    """
    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "nomic-embed-text", dimension: int = 768):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dim = dimension
        self._client = httpx.AsyncClient(timeout=10.0)

    @property
    def dimension(self) -> int:
        return self._dim

    async def generate_embedding(self, text: str) -> list[float]:
        resp = await self._client.post(
            f"{self._base_url}/api/embed",
            json={"model": self._model, "input": text},
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings") or []
        if embeddings:
            return embeddings[0]
        # Fallback to single embed endpoint
        resp2 = await self._client.post(
            f"{self._base_url}/api/embeddings",
            json={"model": self._model, "prompt": text},
        )
        resp2.raise_for_status()
        return resp2.json().get("embedding", [0.0] * self._dim)
