"""
API route handler for POST /v1/extract and POST /v1/extract/{event_id}/retry.
Extracts structured, typed memories from raw captured text or event ID,
verifies character-span citations, handles entity resolution and lineage versioning,
and computes embeddings for hybrid vector retrieval.
"""
from __future__ import annotations
import uuid
import json
import logging
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.domain.extraction import MemoryExtractor
from app.domain.errors import ExtractionError
from app.domain.apply import MemoryApplier
from app.domain.ports import EmbeddingPort

logger = logging.getLogger("lumen.memory.extract_api")
router = APIRouter(prefix="/v1", tags=["extract"])


class ExtractRequest(BaseModel):
    event_id: uuid.UUID
    content: Optional[str] = None
    speaker: str = "user"
    captured_at: Optional[str] = None
    tz: str = "+05:30"
    context: str = "default"


class ExtractResponse(BaseModel):
    event_id: str
    memories_created: int
    memories: list[dict[str, Any]]


def get_db_pool(request: Request):
    storage = getattr(request.app.state, "storage", None)
    pool = getattr(request.app.state, "db_pool", None) or (storage.pool if storage else None)
    if not pool:
        raise RuntimeError("Database pool not available in application state")
    return pool


@router.post("/extract", response_model=ExtractResponse, status_code=status.HTTP_200_OK)
async def extract_and_apply(
    req: ExtractRequest,
    request: Request,
    pool=Depends(get_db_pool),
):
    # 1. Fetch content and context if not provided
    content = req.content
    context_id = None
    async with pool.acquire() as conn:
        if not content:
            ev_row = await conn.fetchrow(
                "SELECT content, context_id FROM events WHERE id = $1",
                req.event_id,
            )
            if not ev_row:
                raise HTTPException(status_code=404, detail=f"Event {req.event_id} not found")
            content = ev_row["content"]
            context_id = ev_row["context_id"]
        else:
            ctx_row = await conn.fetchrow(
                "SELECT id FROM contexts WHERE slug = $1",
                req.context,
            )
            context_id = ctx_row["id"] if ctx_row else None

    if not context_id:
        raise HTTPException(status_code=400, detail=f"Could not resolve context: {req.context}")

    # 2. Run structured LLM extraction with dead-letter staging on failure
    extractor = MemoryExtractor()
    try:
        extraction_data = await extractor.extract(
            text=content,
            speaker=req.speaker,
            captured_at=req.captured_at or "2026-11-21T10:00:00+05:30",
            tz=req.tz,
        )
    except ExtractionError as exc:
        logger.warning(f"Extraction failed for event {req.event_id} [{exc.kind}]: {exc.detail}")
        async with pool.acquire() as conn:
            async with conn.transaction():
                # a. Record extraction attempt
                await conn.execute(
                    """
                    INSERT INTO extraction_attempts (
                        event_id, model, attempt, ok, error_kind, error_detail, raw_output, latency_ms
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    req.event_id,
                    extractor.model,
                    exc.attempt,
                    False,
                    exc.kind,
                    exc.detail,
                    exc.raw[:8192],
                    exc.latency_ms,
                )

                # b. Append SYSTEM audit event
                sys_ev_id = await conn.fetchval(
                    """
                    INSERT INTO events (
                        kind, type, actor, context_id, target_id, caused_by, payload
                    ) VALUES (
                        'SYSTEM', 'extract.failed', 'system:extractor', $1, $2, ARRAY[$2]::uuid[], $3
                    ) RETURNING id
                    """,
                    context_id,
                    req.event_id,
                    json.dumps({
                        "error_kind": exc.kind,
                        "error_detail": exc.detail,
                        "model": extractor.model,
                    }),
                )

                # c. Stage in review_queue
                review_id = await conn.fetchval(
                    """
                    INSERT INTO review_queue (
                        kind, memory_ids, entity_ids, reason, proposal, status, created_event
                    ) VALUES (
                        'failed_extraction', '{}', '{}', $1, $2, 'open', $3
                    ) RETURNING id
                    """,
                    exc.kind,
                    json.dumps({
                        "event_id": str(req.event_id),
                        "error": exc.detail,
                        "error_kind": exc.kind,
                        "raw_output": exc.raw[:2000],
                    }),
                    sys_ev_id,
                )

                # d. Mark jobs entry dead if present
                await conn.execute(
                    """
                    UPDATE jobs SET status = 'dead', last_error = $2
                    WHERE kind = 'extract' AND event_id = $1
                    """,
                    req.event_id,
                    exc.detail,
                )

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "extraction_failed",
                "kind": exc.kind,
                "detail": exc.detail,
                "event_id": str(req.event_id),
                "review_id": str(review_id),
            },
        )

    # 3. Apply extraction (entity resolution, evidence verification, non-destructive versioning)
    applier = MemoryApplier(pool)
    apply_result = await applier.apply_extraction(
        event_id=req.event_id,
        context_id=context_id,
        raw_text=content,
        extraction_data=extraction_data,
    )

    # Record successful attempt in extraction_attempts
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO extraction_attempts (
                event_id, model, attempt, ok, latency_ms
            ) VALUES ($1, $2, $3, $4, $5)
            """,
            req.event_id,
            extractor.model,
            1,
            True,
            0,
        )

    # 4. Generate & store embeddings for created memories (if embedding adapter is available)
    embedding_adapter: Optional[EmbeddingPort] = getattr(request.app.state, "embedding", None)
    if embedding_adapter and apply_result.get("memories"):
        async with pool.acquire() as conn:
            for m in apply_result["memories"]:
                try:
                    stmt = m.get("statement", "")
                    if stmt:
                        vec = await embedding_adapter.generate_embedding(stmt)
                        await conn.execute(
                            "UPDATE memories SET embedding = $1 WHERE id = $2",
                            list(vec),
                            uuid.UUID(m["id"]),
                        )
                except Exception as e:
                    logger.warning(f"Failed to embed memory {m.get('id')}: {e}")

    return ExtractResponse(
        event_id=apply_result["event_id"],
        memories_created=apply_result["memories_created"],
        memories=apply_result["memories"],
    )


@router.post("/extract/{event_id}/retry", response_model=ExtractResponse, status_code=status.HTTP_200_OK)
async def retry_extraction(
    event_id: uuid.UUID,
    request: Request,
    pool=Depends(get_db_pool),
):
    """Re-queues and retries extraction for a failed event."""
    async with pool.acquire() as conn:
        ev_row = await conn.fetchrow(
            "SELECT content, context_id FROM events WHERE id = $1",
            event_id,
        )
        if not ev_row:
            raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    req = ExtractRequest(event_id=event_id, content=ev_row["content"])
    res = await extract_and_apply(req=req, request=request, pool=pool)

    # If extraction succeeded, resolve any open failed_extraction review_queue entries
    if isinstance(res, ExtractResponse):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE review_queue
                SET status = 'accepted'
                WHERE kind = 'failed_extraction'
                  AND proposal->>'event_id' = $1
                  AND status = 'open'
                """,
                str(event_id),
            )

    return res

