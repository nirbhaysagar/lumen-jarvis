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
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.domain.extraction import MemoryExtractor
from app.domain.errors import ExtractionError
from app.domain.apply import MemoryApplier
from app.domain.ports import EmbeddingPort
from app.core.config import settings

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


from app.domain.extraction import MemoryExtractor, ExtractionError
from app.services.extract_service import extract_event


@router.post("/extract", response_model=ExtractResponse, status_code=status.HTTP_200_OK)
async def extract_and_apply(
    req: ExtractRequest,
    request: Request,
    pool=Depends(get_db_pool),
):
    embedding_adapter: Optional[EmbeddingPort] = getattr(request.app.state, "embedding", None)
    extractor = getattr(request.app.state, "extractor", None) or MemoryExtractor()
    try:
        apply_result = await extract_event(
            pool=pool,
            embedding_adapter=embedding_adapter,
            event_id=req.event_id,
            speaker=req.speaker,
            captured_at=req.captured_at,
            tz=req.tz,
            context_slug=req.context,
            content_override=req.content,
            extractor=extractor,
        )
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except ExtractionError as exc:
        # Fetch staged review queue ID
        async with pool.acquire() as conn:
            review_id = await conn.fetchval(
                """
                SELECT id FROM review_queue
                WHERE kind = 'failed_extraction' AND proposal->>'event_id' = $1
                ORDER BY created_at DESC LIMIT 1
                """,
                str(req.event_id),
            )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "extraction_failed",
                "kind": exc.kind,
                "detail": exc.detail,
                "event_id": str(req.event_id),
                "review_id": str(review_id) if review_id else None,
            },
        )

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

