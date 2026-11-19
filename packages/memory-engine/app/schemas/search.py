"""
Pydantic Schemas for Unified Hybrid Search & Reciprocal Rank Fusion (RRF).
"""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class HybridSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=50)
    mode: str = Field(default="hybrid", description="'hybrid', 'dense', or 'lexical'")
    k_rrf: int = Field(default=60, ge=1, le=200, description="RRF rank damping smoothing parameter")


class HybridSearchItem(BaseModel):
    id: uuid.UUID
    entity: str
    attribute: str
    value: Any
    memory_type: str
    epistemic_type: str
    rrf_score: float
    channel_ranks: dict[str, int]
    confidence: float
    valid_from: datetime
    valid_to: datetime | None = None
    source_event_ids: list[uuid.UUID] = Field(default_factory=list)


class HybridSearchResponse(BaseModel):
    query: str
    mode: str
    total_results: int
    results: list[HybridSearchItem]
