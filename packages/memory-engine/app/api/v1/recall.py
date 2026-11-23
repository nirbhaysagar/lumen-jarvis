"""
API route handler for POST /v1/recall.
Executes 5-channel hybrid recall across vector, lexical, entity, graph, and temporal channels.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, List, Optional, Literal
import uuid
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.adapters.storage.pg_recall import PostgresRecallAdapter

router = APIRouter(prefix="/v1", tags=["recall"])


class RecallRequest(BaseModel):
    query: str = Field(..., min_length=1)
    context: str = "default"
    limit: int = Field(default=5, ge=1, le=50)
    threshold: float = Field(default=0.52, ge=0.0, le=1.0)
    time_travel_at: Optional[datetime] = None
    valid_at: Optional[datetime] = None


class RecallResponse(BaseModel):
    status: Literal["ok", "abstain"]
    results: List[dict[str, Any]]
    abstain_reason: Optional[str] = None   # 'no_candidates' | 'low_confidence' | 'insufficient_evidence'
    confidence: Optional[float] = None
    trace_id: uuid.UUID
    count: int = 0


def get_recall_adapter(request: Request) -> PostgresRecallAdapter:
    storage = getattr(request.app.state, "storage", None)
    pool = getattr(request.app.state, "db_pool", None) or (storage.pool if storage else None)
    if not pool:
        raise RuntimeError("Database pool not available in application state")
    embedding = getattr(request.app.state, "embedding", None)
    return PostgresRecallAdapter(pool, embedding_adapter=embedding)


@router.post("/recall", response_model=RecallResponse)
async def recall_memories(
    req: RecallRequest,
    adapter: PostgresRecallAdapter = Depends(get_recall_adapter),
) -> RecallResponse:
    res = await adapter.recall(
        query=req.query,
        context_slug=req.context,
        limit=req.limit,
        threshold=req.threshold,
        time_travel_at=req.time_travel_at,
        valid_at=req.valid_at,
    )
    return RecallResponse(
        status=getattr(res, "status", "ok" if res else "abstain"),
        results=list(res),
        abstain_reason=getattr(res, "abstain_reason", None if res else "no_candidates"),
        confidence=getattr(res, "confidence", None),
        trace_id=getattr(res, "trace_id", uuid.uuid4()),
        count=len(res),
    )
