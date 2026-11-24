"""
Core extraction service for Lumen Memory Engine.
Integrates SavePipeline across HTTP API, background jobs worker, and MCP tools.
"""
from __future__ import annotations
import logging
from typing import Any, Optional
import uuid
import asyncpg

from app.core.config import settings
from app.domain.extraction import MemoryExtractor
from app.domain.ports import EmbeddingPort
from app.pipeline import SavePipeline

logger = logging.getLogger("lumen.services.extract")


async def extract_event(
    pool: asyncpg.Pool,
    embedding_adapter: Optional[EmbeddingPort],
    event_id: uuid.UUID,
    *,
    speaker: str = "user",
    captured_at: Optional[str] = None,
    tz: str = "Asia/Kolkata",
    context_slug: str = "default",
    content_override: Optional[str] = None,
    extractor: Optional[MemoryExtractor] = None,
) -> dict[str, Any]:
    """
    Executes 5-stage save pipeline for an event:
    1. Extract (LLM with schema v2)
    2. Verify (RapidFuzz verbatim quote verification & grounding)
    3. Resolve (Entities and Tier A/B decide)
    4. Commit (Memories, citations, relations, outbox, save_runs)
    5. Embeds created memories (unless secret)
    """
    pipeline = SavePipeline(pool, extractor=extractor)
    report = await pipeline.run(event_id)

    # Embed created memories immediately if adapter is provided
    if embedding_adapter and report.memories:
        async with pool.acquire() as conn:
            for m in report.memories:
                if m.get("sensitivity") != "secret" and m.get("statement"):
                    try:
                        vec = await embedding_adapter.generate_embedding(m["statement"], prefix="search_document")
                        model_name = getattr(embedding_adapter, "_model", None) or getattr(embedding_adapter, "model", settings.EMBEDDING_MODEL)
                        await conn.execute(
                            "UPDATE memories SET embedding = $1, embedding_model = $2 WHERE id = $3",
                            list(vec),
                            model_name,
                            uuid.UUID(m["id"]),
                        )
                    except Exception as e:
                        logger.warning(f"Failed to embed memory {m.get('id')}: {e}")

    return {
        "event_id": str(event_id),
        "memories_created": len(report.memories),
        "memories": report.memories,
        "counts": report.counts,
        "stage_ms": report.stage_ms,
        "dropped": report.dropped,
    }
