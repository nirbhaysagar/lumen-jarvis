"""
Domain Models for Lumen Memory Engine.
Pure business logic and domain entities — zero external framework dependencies.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import uuid


class MemoryType(str, Enum):
    FACT = "fact"
    DECISION = "decision"
    GOAL = "goal"
    CONSTRAINT = "constraint"
    PREFERENCE = "preference"
    QUESTION = "question"
    INSIGHT = "insight"
    RELATIONSHIP = "relationship"


class EpistemicType(str, Enum):
    OBSERVED_FACT = "observed_fact"    # Directly observed event/action
    USER_BELIEF = "user_belief"        # Statement of user thought or worldview
    AI_INFERENCE = "ai_inference"      # Synthesized conjecture by AI
    DECISION = "decision"              # Explicit, binding decision or choice


@dataclass
class MemoryEvent:
    """L0 Raw Immutable Receipt: captured interaction or state change."""
    source: str                         # e.g., 'user_cli', 'chat', 'tool', 'system'
    payload: dict                       # Raw structured content
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    conversation_id: str | None = None
    idempotency_key: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hash: str = field(init=False)

    def __post_init__(self):
        # Deterministic SHA-256 hash ensuring tamper-evident immutability
        raw_str = f"{self.id}:{self.source}:{self.conversation_id}:{json.dumps(self.payload, sort_keys=True)}"
        self.hash = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


@dataclass
class CanonicalFact:
    """L1 Canonical Memory: atomic derived assertion with provenance."""
    entity: str                         # Subject (e.g. 'user', 'project_lumen')
    attribute: str                      # Predicate (e.g. 'preferred_database', 'deadline')
    value: str | dict | list | int      # Object (e.g. 'PostgreSQL', '2026-12-31')
    memory_type: MemoryType = MemoryType.FACT
    epistemic_type: EpistemicType = EpistemicType.OBSERVED_FACT
    confidence: float = 1.0             # 0.0 to 1.0 confidence score
    source_event_ids: list[uuid.UUID] = field(default_factory=list) # Strict Provenance pointers
    valid_from: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    valid_to: datetime | None = None   # None indicates currently active
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class MemorySearchResult:
    """Result of hybrid or semantic memory retrieval with provenance."""
    fact: CanonicalFact
    similarity: float                   # Cosine similarity score (0.0 to 1.0)
    source_citations: list[dict] = field(default_factory=list)


@dataclass
class Entity:
    """Canonical entity node with aliases and embedding representation."""
    canonical_name: str
    category: str = "concept"           # 'person', 'organization', 'technology', 'project', etc.
    aliases: list[str] = field(default_factory=list)
    description: str | None = None
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Predicate:
    """Controlled extensible relationship predicate."""
    id: str                             # Canonical identifier e.g. 'FOUNDED', 'USES'
    inverse_predicate: str | None = None
    description: str | None = None
    is_symmetric: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Triple:
    """Directed relational graph edge linking two canonical entities with bi-temporal validity."""
    subject_id: uuid.UUID
    predicate: str
    object_id: uuid.UUID
    confidence: float = 1.0
    source_event_ids: list[uuid.UUID] = field(default_factory=list)
    valid_from: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    valid_to: datetime | None = None
    metadata: dict = field(default_factory=dict)
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ProposalTier(str, Enum):
    TIER_A = "TIER_A"                   # High confidence, zero contradiction -> Auto-committed
    TIER_B = "TIER_B"                   # Contradiction, ambiguous alias, or belief shift -> Human Review


class ProposalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AUTO_COMMITTED = "AUTO_COMMITTED"


@dataclass
class ReviewProposal:
    """Staged proposal in the Review Queue before or after resolution."""
    proposal_type: str                  # 'NEW_TRIPLE', 'ALIAS_MERGE', 'CONTRADICTION_UPDATE', 'NEW_ENTITY'
    subject_raw: str
    predicate_raw: str
    object_raw: str
    resolved_subject_id: uuid.UUID | None = None
    resolved_predicate: str | None = None
    resolved_object_id: uuid.UUID | None = None
    confidence: float = 1.0
    tier: ProposalTier = ProposalTier.TIER_B
    status: ProposalStatus = ProposalStatus.PENDING
    conflict_reason: str | None = None
    conflicting_triple_id: uuid.UUID | None = None
    source_event_id: uuid.UUID | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reviewed_at: datetime | None = None


@dataclass
class OutboxRecord:
    """Outbox row for transactional, reliable event delivery (e.g. Obsidian sync)."""
    aggregate_type: str                 # 'ENTITY', 'TRIPLE', 'FACT', 'EPISODE'
    aggregate_id: uuid.UUID
    event_type: str                     # 'CREATED', 'UPDATED', 'SUPERSEDED'
    payload: dict
    status: str = "PENDING"             # 'PENDING', 'PROCESSED', 'FAILED'
    retry_count: int = 0
    error: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processed_at: datetime | None = None


@dataclass
class ConsolidationConflict:
    """Contradiction or collision detected during overnight consolidation."""
    conflict_type: str                  # 'OPPOSING_PREDICATES', 'FUNCTIONAL_COLLISION'
    subject_id: uuid.UUID
    subject_name: str
    predicate: str
    conflicting_object_ids: list[uuid.UUID]
    conflicting_object_names: list[str]
    description: str


@dataclass
class AliasMergeCandidate:
    """Entity pair recommended for alias merging based on semantic embedding similarity."""
    source_entity_id: uuid.UUID
    source_name: str
    target_entity_id: uuid.UUID
    target_name: str
    similarity: float


@dataclass
class ConsolidationReport:
    """Summary of overnight consolidation results."""
    conflicts_detected: int = 0
    alias_candidates_found: int = 0
    orphans_detected: int = 0
    proposals_staged: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict = field(default_factory=dict)


@dataclass
class HybridSearchResult:
    """Consolidated search result merged across lexical, dense, and graph channels via RRF."""
    id: uuid.UUID
    entity: str
    attribute: str
    value: Any
    memory_type: MemoryType
    epistemic_type: EpistemicType
    rrf_score: float
    channel_ranks: dict[str, int]        # e.g. {"lexical": 1, "dense": 3, "graph": 2}
    confidence: float = 1.0
    source_event_ids: list[uuid.UUID] = field(default_factory=list)
    valid_from: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    valid_to: datetime | None = None




