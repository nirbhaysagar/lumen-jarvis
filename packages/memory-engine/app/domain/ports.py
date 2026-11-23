"""
Domain Ports (Hexagonal Architecture Interfaces).
Defines abstract contracts for storage and embeddings that external adapters must satisfy.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import uuid
from datetime import datetime
from typing import Sequence
from .models import (
    MemoryEvent,
    CanonicalFact,
    MemorySearchResult,
    MemoryType,
    Entity,
    Triple,
    ReviewProposal,
    ProposalStatus,
    ProposalTier,
    OutboxRecord,
    HybridSearchResult,
)


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
        """Perform cosine similarity vector retrieval against currently active canonical facts."""
        pass

    @abstractmethod
    async def search_facts_at_time(
        self,
        query_embedding: Sequence[float],
        as_of: datetime,
        limit: int = 5,
        threshold: float = 0.01,
        memory_types: Sequence[MemoryType] | None = None,
    ) -> list[MemorySearchResult]:
        """Perform bi-temporal time-travel cosine vector retrieval as of a specific historical timestamp."""
        pass

    @abstractmethod
    async def get_fact_trace(self, fact_id: str | uuid.UUID) -> dict | None:
        """Retrieve the exact provenance chain (source events and historical lineage) for a given fact."""
        pass

    @abstractmethod
    async def save_entity(self, entity: Entity, embedding: Sequence[float] | None = None) -> uuid.UUID:
        """Store or update a canonical entity node with its aliases and optional embedding."""
        pass

    @abstractmethod
    async def find_entity_by_name_or_alias(self, name_or_alias: str) -> Entity | None:
        """Lookup an entity by canonical name or alias array match."""
        pass

    @abstractmethod
    async def get_entity(self, entity_id: uuid.UUID) -> Entity | None:
        """Fetch entity by primary key ID."""
        pass

    @abstractmethod
    async def save_triple(self, triple: Triple) -> uuid.UUID:
        """Persist a canonical relational triple with bi-temporal validity."""
        pass

    @abstractmethod
    async def get_active_triples_for_subject(self, subject_id: uuid.UUID, predicate: str | None = None) -> list[Triple]:
        """Fetch all currently active triples for a subject entity (valid_to IS NULL)."""
        pass

    @abstractmethod
    async def stage_review_proposal(self, proposal: ReviewProposal) -> uuid.UUID:
        """Insert a proposed triple or entity change into the Review Queue staging table."""
        pass

    @abstractmethod
    async def list_review_proposals(
        self,
        status: ProposalStatus | None = None,
        tier: ProposalTier | None = None,
        limit: int = 50,
    ) -> list[ReviewProposal]:
        """List staged proposals from the review queue filtered by status and tier."""
        pass

    @abstractmethod
    async def resolve_review_proposal(
        self,
        proposal_id: uuid.UUID,
        decision: ProposalStatus,
    ) -> bool:
        """Approve or reject a staged proposal in the review queue."""
        pass

    @abstractmethod
    async def traverse_graph(
        self,
        start_entity_id: uuid.UUID,
        max_depth: int = 2,
    ) -> list[dict]:
        """Traverse connected entities and triples up to max_depth using recursive SQL."""
        pass

    @abstractmethod
    async def fetch_pending_outbox(self, limit: int = 50) -> list[OutboxRecord]:
        """Fetch pending records from the transactional outbox ordered by created_at ASC."""
        pass

    @abstractmethod
    async def mark_outbox_processed(self, outbox_id: uuid.UUID) -> bool:
        """Mark an outbox record as PROCESSED with current timestamp."""
        pass

    @abstractmethod
    async def mark_outbox_failed(self, outbox_id: uuid.UUID, error: str) -> bool:
        """Mark an outbox record as FAILED with an error message and increment retry count."""
        pass

    @abstractmethod
    async def get_outbox_stats(self) -> dict[str, int]:
        """Return counts of PENDING, PROCESSED, and FAILED outbox events."""
        pass

    @abstractmethod
    async def get_orphan_entities(self, max_degree: int = 1) -> list[Entity]:
        """Find entity nodes with degree (in + out edges) less than or equal to max_degree."""
        pass

    @abstractmethod
    async def find_high_similarity_entity_pairs(
        self,
        threshold: float = 0.88,
        limit: int = 20,
    ) -> list[dict]:
        """Find distinct entity pairs whose embedding cosine similarity exceeds threshold."""
        pass

    @abstractmethod
    async def find_contradictory_triples(self) -> list[dict]:
        """Detect opposing active assertions (e.g. PREFERS vs DISLIKES on the same subject & object)."""
        pass

    @abstractmethod
    async def find_multi_value_functional_collisions(
        self,
        predicates: list[str] | None = None,
    ) -> list[dict]:
        """Detect functional predicates with multiple active conflicting target values for a single subject."""
        pass

    @abstractmethod
    async def search_lexical_facts(
        self,
        query: str,
        limit: int = 20,
    ) -> list[CanonicalFact]:
        """Perform full-text BM25 / tsvector and trigram search against active facts."""
        pass

    @abstractmethod
    async def search_graph_facts(
        self,
        query: str,
        limit: int = 20,
    ) -> list[CanonicalFact]:
        """Retrieve facts associated with entities found via knowledge graph matching and 1-hop traversal."""
        pass

    @abstractmethod
    async def hybrid_search(
        self,
        query: str,
        query_embedding: Sequence[float],
        limit: int = 5,
        k_rrf: int = 60,
    ) -> list[HybridSearchResult]:
        """Unified hybrid search merging lexical, dense vector, and graph channels with Reciprocal Rank Fusion."""
        pass


class EmbeddingPort(ABC):
    """Port for transforming human text into high-dimensional vector space."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the vector dimensionality (e.g. 1536)."""
        pass

    @abstractmethod
    async def generate_embedding(self, text: str, prefix: str | None = None) -> list[float]:
        """Produce a normalized vector embedding for given text."""
        pass
