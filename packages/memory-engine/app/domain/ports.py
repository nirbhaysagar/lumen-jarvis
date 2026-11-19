"""
Domain Ports (Hexagonal Architecture Interfaces).
Defines abstract contracts for storage and embeddings that external adapters must satisfy.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import uuid
from typing import Sequence
from .models import MemoryEvent, CanonicalFact, MemorySearchResult, MemoryType


class StoragePort(ABC):
    """Port for persistent state, raw event ledger, and vector retrieval."""

    @abstractmethod
    async def initialize_schema(self) -> None:
        """Create tables and vector indices if they do not exist."""
        pass

    @abstractmethod
    async def save_event(self, event: MemoryEvent) -> uuid.UUID:
        """Store an immutable raw receipt (idempotently)."""
        pass

    @abstractmethod
    async def save_fact(self, fact: CanonicalFact, embedding: Sequence[float]) -> uuid.UUID:
        """Store a canonical derived fact alongside its dense embedding vector."""
        pass

    @abstractmethod
    async def search_facts(
        self,
        query_embedding: Sequence[float],
        limit: int = 5,
        threshold: float = 0.5,
        memory_types: Sequence[MemoryType] | None = None,
    ) -> list[MemorySearchResult]:
        """Perform cosine similarity vector retrieval against canonical facts."""
        pass

    @abstractmethod
    async def get_fact_trace(self, fact_id: uuid.UUID) -> dict | None:
        """Retrieve the exact provenance chain (source events) for a given fact."""
        pass


class EmbeddingPort(ABC):
    """Port for transforming human text into high-dimensional vector space."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the vector dimensionality (e.g. 1536)."""
        pass

    @abstractmethod
    async def generate_embedding(self, text: str) -> list[float]:
        """Produce a normalized vector embedding for given text."""
        pass
