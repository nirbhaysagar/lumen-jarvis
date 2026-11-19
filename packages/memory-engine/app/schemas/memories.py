"""
Border Validation Schemas for Memory Ingestion, Retrieval, Time-Travel and Tracing.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from datetime import datetime
import uuid
from typing import Any
from app.domain.models import MemoryType, EpistemicType


class CreateFactRequest(BaseModel):
    entity: str = Field(..., description="Entity or topic the fact describes (e.g. 'user', 'lumen_core')")
    attribute: str = Field(..., description="Property or attribute (e.g. 'preferred_database', 'deadline')")
    value: Any = Field(..., description="Value of the attribute")
    memory_type: MemoryType = Field(default=MemoryType.FACT)
    epistemic_type: EpistemicType = Field(default=EpistemicType.OBSERVED_FACT)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_event_ids: list[uuid.UUID] = Field(default_factory=list, description="IDs of source events for provenance")
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class FactResponse(BaseModel):
    id: uuid.UUID
    entity: str
    attribute: str
    value: Any
    memory_type: MemoryType
    epistemic_type: EpistemicType
    confidence: float
    source_event_ids: list[uuid.UUID]
    valid_from: datetime
    valid_to: datetime | None


class SearchMemoryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language search query")
    limit: int = Field(default=5, ge=1, le=50, description="Max memories to return")
    threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Cosine similarity threshold")
    types: list[MemoryType] | None = Field(default=None, description="Optional filter by MemoryType")


class TimeTravelRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language search query")
    as_of: datetime = Field(..., description="Historical point in time to reconstruct state as of")
    limit: int = Field(default=5, ge=1, le=50, description="Max memories to return")
    threshold: float = Field(default=0.01, ge=0.0, le=1.0, description="Cosine similarity threshold")
    types: list[MemoryType] | None = Field(default=None, description="Optional filter by MemoryType")


class SearchMemoryItem(BaseModel):
    id: uuid.UUID
    entity: str
    attribute: str
    value: Any
    memory_type: MemoryType
    epistemic_type: EpistemicType
    confidence: float
    similarity: float
    source_event_ids: list[uuid.UUID]
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class SearchMemoryResponse(BaseModel):
    query: str
    total: int
    execution_time_ms: float
    results: list[SearchMemoryItem]


class TraceResponse(BaseModel):
    fact: FactResponse
    source_events: list[dict]
    history_lineage: list[FactResponse] = Field(default_factory=list)
