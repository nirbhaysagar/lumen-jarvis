"""
API route handler for POST /v1/extract.
Extracts structured, typed memories from raw captured text or event ID,
verifies character-span citations, handles entity resolution and lineage versioning,
and computes embeddings for hybrid vector retrieval.
"""
from __future__ import annotations
import uuid
import logging
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.domain.extraction import MemoryExtractor
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
) -> ExtractResponse:
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

    # 2. Run structured LLM extraction
    extractor = MemoryExtractor()
    extraction_data = await extractor.extract(
        text=content,
        speaker=req.speaker,
        captured_at=req.captured_at or "2026-11-21T10:00:00+05:30",
        tz=req.tz,
    )

    # 3. Apply extraction (entity resolution, evidence verification, non-destructive versioning)
    applier = MemoryApplier(pool)
    apply_result = await applier.apply_extraction(
        event_id=req.event_id,
        context_id=context_id,
        raw_text=content,
        extraction_data=extraction_data,
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
