"""
FastAPI REST Endpoints for Lumen Memory Engine (v1).
"""
import time
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Annotated

from app.domain.models import (
    MemoryEvent,
    CanonicalFact,
    Entity,
    Triple,
    ProposalStatus,
    ProposalTier,
)
from app.domain.ports import StoragePort, EmbeddingPort
from app.domain.resolver import EntityResolver
from app.domain.consolidator import KnowledgeConsolidator
from app.schemas.events import CreateEventRequest, EventResponse
from app.schemas.memories import (
    CreateFactRequest,
    FactResponse,
    SearchMemoryRequest,
    SearchMemoryResponse,
    SearchMemoryItem,
    TimeTravelRequest,
    TraceResponse,
)
from app.schemas.graph import (
    CreateEntityRequest,
    EntityResponse,
    ProposeTripleRequest,
    ProposeTripleResponse,
    ReviewProposalResponse,
    ResolveProposalRequest,
    GraphTraversalItem,
    GraphTraversalResponse,
    TripleResponse,
)
from app.schemas.outbox import (
    OutboxItem,
    OutboxPendingResponse,
    OutboxStatsResponse,
    OutboxFailRequest,
)
from app.schemas.consolidation import (
    ConsolidationReportResponse,
    OrphanEntityItem,
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


@router.post("/memories/time-travel", response_model=SearchMemoryResponse)
async def time_travel_search(
    req: TimeTravelRequest,
    storage: Annotated[StoragePort, Depends(get_storage)],
    embedder: Annotated[EmbeddingPort, Depends(get_embedding)],
):
    """
    Bi-Temporal Time-Travel Retrieval:
    Reconstructs the agent's memory state exactly as it existed at `as_of` in history.
    """
    start_time = time.perf_counter()
    query_vector = await embedder.generate_embedding(req.query)

    results = await storage.search_facts_at_time(
        query_embedding=query_vector,
        as_of=req.as_of,
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
            valid_from=r.fact.valid_from,
            valid_to=r.fact.valid_to,
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
    fact_id: str,
    storage: Annotated[StoragePort, Depends(get_storage)],
):
    """
    Trace API: Returns the fact, its full provenance DAG back to source events,
    and historical version lineage (supports both exact UUID and short prefix).
    """
    trace_data = await storage.get_fact_trace(fact_id)
    if not trace_data:
        raise HTTPException(status_code=404, detail="Fact not found")
    return trace_data


# --- KNOWLEDGE GRAPH & REVIEW QUEUE ENDPOINTS ---

@router.post("/entities", response_model=EntityResponse)
async def create_or_update_entity(
    req: CreateEntityRequest,
    storage: Annotated[StoragePort, Depends(get_storage)],
    embedder: Annotated[EmbeddingPort, Depends(get_embedding)],
):
    """Register or update a canonical entity with aliases and vector embedding."""
    resolver = EntityResolver(storage=storage, embedder=embedder)
    entity = await resolver.resolve_or_create_entity(
        raw_name=req.canonical_name,
        category=req.category,
    )
    if req.aliases:
        for a in req.aliases:
            if a.lower() not in [x.lower() for x in entity.aliases]:
                entity.aliases.append(a)
        await storage.save_entity(entity)

    return EntityResponse(
        id=entity.id,
        canonical_name=entity.canonical_name,
        category=entity.category,
        aliases=entity.aliases,
        description=entity.description,
        metadata=entity.metadata,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


@router.get("/entities/{name_or_id}")
async def get_entity(
    name_or_id: str,
    storage: Annotated[StoragePort, Depends(get_storage)],
):
    """Retrieve an entity by canonical name, alias, or UUID."""
    try:
        e_uuid = uuid.UUID(name_or_id)
        entity = await storage.get_entity(e_uuid)
    except ValueError:
        entity = await storage.find_entity_by_name_or_alias(name_or_id)

    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    return EntityResponse(
        id=entity.id,
        canonical_name=entity.canonical_name,
        category=entity.category,
        aliases=entity.aliases,
        description=entity.description,
        metadata=entity.metadata,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


@router.post("/graph/propose", response_model=ProposeTripleResponse)
async def propose_triple(
    req: ProposeTripleRequest,
    storage: Annotated[StoragePort, Depends(get_storage)],
    embedder: Annotated[EmbeddingPort, Depends(get_embedding)],
):
    """
    Submits a candidate triple assertion.
    Resolves subject/object aliases, checks predicate ontology, and audits for contradictions.
    Tier A (no conflict) auto-commits; Tier B (contradiction) stages into Review Queue.
    """
    resolver = EntityResolver(storage=storage, embedder=embedder)
    proposal, committed_triple = await resolver.stage_or_commit_triple(
        subject_raw=req.subject,
        predicate_raw=req.predicate,
        object_raw=req.object,
        source_event_id=req.source_event_id,
        confidence=req.confidence,
        subject_category=req.subject_category,
        object_category=req.object_category,
    )

    prop_resp = ReviewProposalResponse(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        subject_raw=proposal.subject_raw,
        predicate_raw=proposal.predicate_raw,
        object_raw=proposal.object_raw,
        resolved_subject_id=proposal.resolved_subject_id,
        resolved_predicate=proposal.resolved_predicate,
        resolved_object_id=proposal.resolved_object_id,
        confidence=proposal.confidence,
        tier=proposal.tier.value if hasattr(proposal.tier, "value") else str(proposal.tier),
        status=proposal.status.value if hasattr(proposal.status, "value") else str(proposal.status),
        conflict_reason=proposal.conflict_reason,
        conflicting_triple_id=proposal.conflicting_triple_id,
        source_event_id=proposal.source_event_id,
        created_at=proposal.created_at,
        reviewed_at=proposal.reviewed_at,
    )

    triple_resp = None
    if committed_triple:
        triple_resp = TripleResponse(
            id=committed_triple.id,
            subject_id=committed_triple.subject_id,
            predicate=committed_triple.predicate,
            object_id=committed_triple.object_id,
            confidence=committed_triple.confidence,
            source_event_ids=committed_triple.source_event_ids,
            valid_from=committed_triple.valid_from,
            valid_to=committed_triple.valid_to,
        )

    action = "AUTO_COMMITTED" if committed_triple else "STAGED_FOR_REVIEW"
    return ProposeTripleResponse(
        proposal=prop_resp,
        triple=triple_resp,
        action_taken=action,
    )


@router.get("/graph/review-queue", response_model=list[ReviewProposalResponse])
async def list_review_queue(
    storage: Annotated[StoragePort, Depends(get_storage)],
    status: str | None = None,
    tier: str | None = None,
    limit: int = 50,
):
    """Lists staged proposals in the Review Queue."""
    stat_enum = ProposalStatus(status) if status else None
    tier_enum = ProposalTier(tier) if tier else None
    proposals = await storage.list_review_proposals(status=stat_enum, tier=tier_enum, limit=limit)
    return [
        ReviewProposalResponse(
            id=p.id,
            proposal_type=p.proposal_type,
            subject_raw=p.subject_raw,
            predicate_raw=p.predicate_raw,
            object_raw=p.object_raw,
            resolved_subject_id=p.resolved_subject_id,
            resolved_predicate=p.resolved_predicate,
            resolved_object_id=p.resolved_object_id,
            confidence=p.confidence,
            tier=p.tier.value if hasattr(p.tier, "value") else str(p.tier),
            status=p.status.value if hasattr(p.status, "value") else str(p.status),
            conflict_reason=p.conflict_reason,
            conflicting_triple_id=p.conflicting_triple_id,
            source_event_id=p.source_event_id,
            created_at=p.created_at,
            reviewed_at=p.reviewed_at,
        )
        for p in proposals
    ]


@router.post("/graph/review-queue/{proposal_id}/resolve")
async def resolve_review_proposal(
    proposal_id: uuid.UUID,
    req: ResolveProposalRequest,
    storage: Annotated[StoragePort, Depends(get_storage)],
):
    """Authorize (/approve) or reject (/reject) a staged proposal in the review queue."""
    decision_clean = req.decision.strip().upper()
    if decision_clean not in {"APPROVED", "REJECTED"}:
        raise HTTPException(status_code=400, detail="Decision must be 'APPROVED' or 'REJECTED'")

    decision_enum = ProposalStatus.APPROVED if decision_clean == "APPROVED" else ProposalStatus.REJECTED
    success = await storage.resolve_review_proposal(proposal_id, decision=decision_enum)
    if not success:
        raise HTTPException(status_code=404, detail="Proposal not found in review queue")

    return {
        "proposal_id": str(proposal_id),
        "status": decision_clean,
        "message": f"Proposal successfully {decision_clean.lower()}.",
    }


@router.get("/graph/traverse/{entity_name_or_id}", response_model=GraphTraversalResponse)
async def traverse_graph(
    entity_name_or_id: str,
    storage: Annotated[StoragePort, Depends(get_storage)],
    max_depth: int = 2,
):
    """Walks the relational graph starting from an entity up to max_depth using recursive SQL."""
    try:
        e_uuid = uuid.UUID(entity_name_or_id)
        entity = await storage.get_entity(e_uuid)
    except ValueError:
        entity = await storage.find_entity_by_name_or_alias(entity_name_or_id)

    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    edges = await storage.traverse_graph(entity.id, max_depth=max_depth)
    return GraphTraversalResponse(
        start_entity_id=entity.id,
        total_edges=len(edges),
        edges=[
            GraphTraversalItem(
                triple_id=e["triple_id"],
                subject_id=e["subject_id"],
                predicate=e["predicate"],
                object_id=e["object_id"],
                subject_name=e["subject_name"],
                object_name=e["object_name"],
                depth=e["depth"],
            )
            for e in edges
        ],
    )


# ==========================================
# Transactional Outbox Endpoints
# ==========================================

@router.get("/outbox/pending", response_model=OutboxPendingResponse)
async def list_pending_outbox(
    storage: Annotated[StoragePort, Depends(get_storage)],
    limit: int = 50,
):
    """Fetch pending events from the transactional outbox waiting for sync (e.g. to Obsidian)."""
    rows = await storage.fetch_pending_outbox(limit=limit)
    return OutboxPendingResponse(
        items=[
            OutboxItem(
                id=r.id,
                aggregate_type=r.aggregate_type,
                aggregate_id=r.aggregate_id,
                event_type=r.event_type,
                payload=r.payload,
                status=r.status,
                retry_count=r.retry_count,
                error=r.error,
                created_at=r.created_at,
                processed_at=r.processed_at,
            )
            for r in rows
        ],
        count=len(rows),
    )


@router.post("/outbox/{outbox_id}/ack")
async def ack_outbox_event(
    outbox_id: uuid.UUID,
    storage: Annotated[StoragePort, Depends(get_storage)],
):
    """Mark an outbox event as PROCESSED after successful vault sync or delivery."""
    success = await storage.mark_outbox_processed(outbox_id)
    if not success:
        raise HTTPException(status_code=404, detail="Outbox event not found")
    return {"outbox_id": str(outbox_id), "status": "PROCESSED"}


@router.post("/outbox/{outbox_id}/fail")
async def fail_outbox_event(
    outbox_id: uuid.UUID,
    req: OutboxFailRequest,
    storage: Annotated[StoragePort, Depends(get_storage)],
):
    """Mark an outbox event as FAILED with an error message and increment its retry count."""
    success = await storage.mark_outbox_failed(outbox_id, req.error)
    if not success:
        raise HTTPException(status_code=404, detail="Outbox event not found")
    return {"outbox_id": str(outbox_id), "status": "FAILED", "error": req.error}


@router.get("/outbox/stats", response_model=OutboxStatsResponse)
async def get_outbox_stats(
    storage: Annotated[StoragePort, Depends(get_storage)],
):
    """Fetch counts of PENDING, PROCESSED, and FAILED outbox events."""
    stats = await storage.get_outbox_stats()
    return OutboxStatsResponse(
        pending=stats.get("PENDING", 0),
        processed=stats.get("PROCESSED", 0),
        failed=stats.get("FAILED", 0),
        total=stats.get("TOTAL", 0),
    )


# ==========================================
# Knowledge Graph Consolidation Endpoints
# ==========================================

@router.post("/graph/consolidate", response_model=ConsolidationReportResponse)
async def run_consolidation(
    storage: Annotated[StoragePort, Depends(get_storage)],
):
    """Run autonomous overnight consolidation patrol to detect contradictions and stage proposals."""
    consolidator = KnowledgeConsolidator(storage=storage)
    report = await consolidator.run_consolidation()
    return ConsolidationReportResponse(
        conflicts_detected=report.conflicts_detected,
        alias_candidates_found=report.alias_candidates_found,
        orphans_detected=report.orphans_detected,
        proposals_staged=report.proposals_staged,
        timestamp=report.timestamp,
        details=report.details,
    )


@router.get("/graph/orphans", response_model=list[OrphanEntityItem])
async def list_orphan_entities(
    storage: Annotated[StoragePort, Depends(get_storage)],
    max_degree: int = 1,
):
    """List entity nodes with degree (in + out connections) <= max_degree."""
    orphans = await storage.get_orphan_entities(max_degree=max_degree)
    return [
        OrphanEntityItem(
            id=str(o.id),
            canonical_name=o.canonical_name,
            category=o.category,
            aliases=o.aliases,
        )
        for o in orphans
    ]


@router.get("/graph/similarity-clusters")
async def list_similarity_clusters(
    storage: Annotated[StoragePort, Depends(get_storage)],
    threshold: float = 0.88,
    limit: int = 20,
):
    """List entity pairs whose vector embeddings have high cosine similarity."""
    pairs = await storage.find_high_similarity_entity_pairs(threshold=threshold, limit=limit)
    return {"clusters": pairs, "count": len(pairs)}



