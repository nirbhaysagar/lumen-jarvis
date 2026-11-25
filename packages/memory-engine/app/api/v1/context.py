"""
Context Pack API for Grounded LLM Answering (Phase 0.6 - K1).
Endpoint: POST /v1/context
Generates a structured ContextPack containing verified long-term memories,
character-grounded citations, and a token-budgeted prompt injection.
"""
from __future__ import annotations
from datetime import datetime
import json
from typing import Any, List, Optional, Literal
import uuid
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.adapters.storage.pg_recall import PostgresRecallAdapter
from app.api.v1.recall import get_recall_adapter

router = APIRouter(prefix="/v1", tags=["context"])


class ContextRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Question or conversational context")
    context: str = Field(default="default", description="Retrieval context slug (default, work, personal)")
    budget_tokens: int = Field(default=2048, ge=128, le=8192, description="Maximum token budget for prompt injection")
    threshold: float = Field(default=0.40, ge=0.0, le=1.0, description="Abstention confidence threshold")
    time_travel_at: Optional[datetime] = None
    valid_at: Optional[datetime] = None
    include_raw: bool = False


class CitationItem(BaseModel):
    citation_id: str
    quote: str
    event_id: str
    said_at: Optional[str] = None
    source_title: Optional[str] = None


class ContextPack(BaseModel):
    query: str
    status: Literal["ok", "abstain"]
    abstain_reason: Optional[str] = None
    confidence: Optional[float] = None
    trace_id: uuid.UUID
    directives: List[dict[str, Any]] = Field(default_factory=list)
    verified_facts: List[str]
    citations: List[CitationItem]
    relevant_memories: List[dict[str, Any]]
    prompt_injection: str
    token_count: int


def estimate_tokens(text: str) -> int:
    """Estimates token count using character length ratio (~4 chars per token)."""
    return max(1, len(text) // 4)


def assemble_prompt_injection(
    facts: list[str],
    citations: list[CitationItem],
    status: str,
    directives: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Renders the markdown prompt injection block enforcing strict citation grounding and procedural directives."""
    sections = []

    # 1. Procedural Directives Tier (Highest Priority)
    if directives:
        d_lines = [
            "## Operating Directives & Guidelines",
            "You MUST strictly adhere to the following user rules and operational constraints:",
        ]
        for idx, d in enumerate(directives):
            d_type = d.get("directive_type") or "rule"
            priority = d.get("priority", 5)
            body = d.get("body") or d.get("title", "")
            d_lines.append(f"- [D{idx+1}] ({d_type}, priority {priority}): {body}")
        sections.append("\n".join(d_lines))

    # 2. Recalled Long-Term Memories
    if status == "abstain" or not facts:
        sections.append(
            "## Recalled Long-Term Memories\n"
            "[No verified memories met confidence threshold. If asked about unknown personal facts, you MUST state honestly: 'I do not have memory of this.']"
        )
    else:
        m_lines = [
            "## Recalled Long-Term Memories",
            "The following verified facts were retrieved from user memory:",
        ]
        for fact in facts:
            m_lines.append(f"- {fact}")

        m_lines.extend([
            "",
            "### Grounding Instructions:",
            "1. Answer the user's inquiry based STRICTLY on the verified facts above.",
            "2. You MUST cite the source tag (e.g. [M1], [M2]) for any factual statement made.",
            "3. If the memories above do not contain the answer, state honestly: 'I do not have memory of this.'",
        ])
        sections.append("\n".join(m_lines))

    return "\n\n".join(sections)


@router.post("/context", response_model=ContextPack)
async def generate_context_pack(
    req: ContextRequest,
    adapter: PostgresRecallAdapter = Depends(get_recall_adapter),
) -> ContextPack:
    # 0. Fetch active procedural directives
    from app.services.directives import DirectivesService
    directives_svc = DirectivesService(adapter.pool)
    async with adapter.pool.acquire() as conn:
        ctx_row = await conn.fetchrow("SELECT id FROM contexts WHERE slug = $1", req.context)
        ctx_id = ctx_row["id"] if ctx_row else None
    active_directives = await directives_svc.list_directives(context_id=ctx_id, status="active", limit=15)

    # 1. Fetch recalled memories with reranking
    recalled = await adapter.recall(
        query=req.query,
        context_slug=req.context,
        limit=10,
        threshold=req.threshold,
        time_travel_at=req.time_travel_at,
        valid_at=req.valid_at,
    )

    trace_id = getattr(recalled, "trace_id", uuid.uuid4())
    status = getattr(recalled, "status", "ok" if recalled else "abstain")
    abstain_reason = getattr(recalled, "abstain_reason", None if recalled else "no_candidates")
    confidence = getattr(recalled, "confidence", None)

    if status == "abstain" or not recalled:
        empty_injection = assemble_prompt_injection([], [], "abstain", directives=active_directives)
        return ContextPack(
            query=req.query,
            status="abstain",
            abstain_reason=abstain_reason,
            confidence=confidence,
            trace_id=trace_id,
            directives=active_directives,
            verified_facts=[],
            citations=[],
            relevant_memories=[],
            prompt_injection=empty_injection,
            token_count=estimate_tokens(empty_injection),
        )

    # 2. Greedily pack memories within budget_tokens
    packed_facts: list[str] = []
    packed_citations: list[CitationItem] = []
    packed_memories: list[dict[str, Any]] = []

    for idx, mem in enumerate(recalled):
        cid = f"M{idx+1}"
        fact_line = f"[{cid}] {mem['statement']}"
        
        # Build citations for this memory
        mem_citations = []
        for c in mem.get("citations", []):
            mem_citations.append(
                CitationItem(
                    citation_id=cid,
                    quote=c["quote"],
                    event_id=c["event_id"],
                    said_at=c.get("said_at"),
                    source_title=c.get("source_title"),
                )
            )

        # Check candidate prompt length if added
        cand_facts = packed_facts + [fact_line]
        cand_citations = packed_citations + mem_citations
        cand_injection = assemble_prompt_injection(cand_facts, cand_citations, "ok", directives=active_directives)
        cand_tokens = estimate_tokens(cand_injection)

        if cand_tokens <= req.budget_tokens or len(packed_memories) == 0:
            packed_facts = cand_facts
            packed_citations = cand_citations
            packed_memories.append(mem)
        else:
            break

    final_injection = assemble_prompt_injection(packed_facts, packed_citations, "ok", directives=active_directives)
    total_tokens = estimate_tokens(final_injection)

    return ContextPack(
        query=req.query,
        status="ok",
        abstain_reason=None,
        confidence=confidence,
        trace_id=trace_id,
        directives=active_directives,
        verified_facts=packed_facts,
        citations=packed_citations,
        relevant_memories=packed_memories,
        prompt_injection=final_injection,
        token_count=total_tokens,
    )
