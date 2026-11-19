"""
FastAPI REST Endpoints for Lumen Memory Engine (v1).
"""
import time
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Annotated

from app.domain.models import MemoryEvent, CanonicalFact
from app.domain.ports import StoragePort, EmbeddingPort
from app.schemas.events import CreateEventRequest, EventResponse
from app.schemas.memories import (
    CreateFactRequest,
    FactResponse,
    SearchMemoryRequest,
    SearchMemoryResponse,
    SearchMemoryItem,
    TraceResponse,
)

router = APIRouter(prefix="/api/v1")


def get_storage(request: Request) -> StoragePort:
    return request.app.state.storage


def get_embedding(request: Request) -> EmbeddingPort:
    return request.app.state.embedding


@router.post("/events", response_model=EventResponse)
async def create_event(
    req: CreateEventRequest,
    storage: Annotated[StoragePort, Depends(get_storage)],
):
    """
    Ingest an immutable raw event (L0 receipt).
    Guarantees idempotency via `idempotency_key`.
    """
    event = MemoryEvent(
        source=req.source,
        payload=req.payload,
        conversation_id=req.conversation_id,
        idempotency_key=req.idempotency_key,
    )
    saved_id = await storage.save_event(event)
    return EventResponse(
        id=saved_id,
        source=event.source,
        conversation_id=event.conversation_id,
        created_at=event.created_at,
        hash=event.hash,
        idempotent_hit=(saved_id != event.id),
    )


@router.post("/facts", response_model=FactResponse)
async def create_fact(
    req: CreateFactRequest,
    storage: Annotated[StoragePort, Depends(get_storage)],
    embedder: Annotated[EmbeddingPort, Depends(get_embedding)],
):
    """
    Store a canonical derived fact (L1) with vector embedding and provenance pointers.
    """
    valid_from = req.valid_from or datetime.now(timezone.utc)
    fact = CanonicalFact(
        entity=req.entity,
        attribute=req.attribute,
        value=req.value,
        memory_type=req.memory_type,
        epistemic_type=req.epistemic_type,
        confidence=req.confidence,
        source_event_ids=req.source_event_ids,
        valid_from=valid_from,
        valid_to=req.valid_to,
    )
    
    # Generate vector embedding for semantic search
    fact_text = f"{fact.entity} {fact.attribute}: {fact.value}"
    embedding = await embedder.generate_embedding(fact_text)

    saved_id = await storage.save_fact(fact, embedding)
    return FactResponse(
        id=saved_id,
        entity=fact.entity,
        attribute=fact.attribute,
        value=fact.value,
        memory_type=fact.memory_type,
        epistemic_type=fact.epistemic_type,
        confidence=fact.confidence,
        source_event_ids=fact.source_event_ids,
        valid_from=fact.valid_from,
        valid_to=fact.valid_to,
    )


@router.post("/memories/search", response_model=SearchMemoryResponse)
async def search_memories(
    req: SearchMemoryRequest,
    storage: Annotated[StoragePort, Depends(get_storage)],
    embedder: Annotated[EmbeddingPort, Depends(get_embedding)],
):
    """
    Perform sub-second semantic vector retrieval against stored canonical facts.
    """
    start_time = time.perf_counter()
    query_vector = await embedder.generate_embedding(req.query)

    results = await storage.search_facts(
        query_embedding=query_vector,
        limit=req.limit,
        threshold=req.threshold,
        memory_types=req.types,
    )
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    items = [
        SearchMemoryItem(
            id=r.fact.id,
            entity=r.fact.entity,
            attribute=r.fact.attribute,
            value=r.fact.value,
            memory_type=r.fact.memory_type,
            epistemic_type=r.fact.epistemic_type,
            confidence=r.fact.confidence,
            similarity=round(r.similarity, 4),
            source_event_ids=r.fact.source_event_ids,
        )
        for r in results
    ]

    return SearchMemoryResponse(
        query=req.query,
        total=len(items),
        execution_time_ms=round(elapsed_ms, 2),
        results=items,
    )


@router.get("/trace/{fact_id}", response_model=TraceResponse)
async def get_fact_trace(
    fact_id: uuid.UUID,
    storage: Annotated[StoragePort, Depends(get_storage)],
):
    """
    Trace API: Returns the fact and the exact raw source events that generated it.
    """
    trace_data = await storage.get_fact_trace(fact_id)
    if not trace_data:
        raise HTTPException(status_code=404, detail="Fact not found")
    return trace_data
