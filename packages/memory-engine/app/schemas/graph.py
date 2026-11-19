"""
Pydantic Schemas for Knowledge Graph Entities, Triples, and Review Queue API.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel, Field


class CreateEntityRequest(BaseModel):
    canonical_name: str = Field(..., description="Canonical name of the entity node")
    category: str = Field(default="concept", description="Category: person, organization, technology, project, etc.")
    aliases: list[str] = Field(default_factory=list, description="Alternative names or abbreviations")
    description: str | None = Field(default=None, description="Detailed description")
    metadata: dict = Field(default_factory=dict)


class EntityResponse(BaseModel):
    id: uuid.UUID
    canonical_name: str
    category: str
    aliases: list[str]
    description: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ProposeTripleRequest(BaseModel):
    subject: str = Field(..., description="Subject entity surface name or alias")
    predicate: str = Field(..., description="Relationship verb/predicate")
    object: str = Field(..., description="Object entity surface name or alias")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_event_id: uuid.UUID | None = None
    subject_category: str = "concept"
    object_category: str = "concept"


class TripleResponse(BaseModel):
    id: uuid.UUID
    subject_id: uuid.UUID
    predicate: str
    object_id: uuid.UUID
    confidence: float
    source_event_ids: list[uuid.UUID]
    valid_from: datetime
    valid_to: datetime | None = None


class ReviewProposalResponse(BaseModel):
    id: uuid.UUID
    proposal_type: str
    subject_raw: str
    predicate_raw: str
    object_raw: str
    resolved_subject_id: uuid.UUID | None = None
    resolved_predicate: str | None = None
    resolved_object_id: uuid.UUID | None = None
    confidence: float
    tier: str
    status: str
    conflict_reason: str | None = None
    conflicting_triple_id: uuid.UUID | None = None
    source_event_id: uuid.UUID | None = None
    created_at: datetime
    reviewed_at: datetime | None = None


class ProposeTripleResponse(BaseModel):
    proposal: ReviewProposalResponse
    triple: TripleResponse | None = None
    action_taken: str  # "AUTO_COMMITTED" or "STAGED_FOR_REVIEW"


class ResolveProposalRequest(BaseModel):
    decision: str = Field(..., description="'APPROVED' or 'REJECTED'")


class GraphTraversalItem(BaseModel):
    triple_id: str
    subject_id: str
    predicate: str
    object_id: str
    subject_name: str
    object_name: str
    depth: int


class GraphTraversalResponse(BaseModel):
    start_entity_id: uuid.UUID
    total_edges: int
    edges: list[GraphTraversalItem]
