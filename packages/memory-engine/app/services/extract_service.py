"""
Core extraction service for Lumen Memory Engine.
Extracts structured memories from events using Ollama JSON schema extraction,
verifies evidence, applies non-destructive versioning, and computes vector embeddings.
Shared across HTTP API, background jobs worker, and MCP tools.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional
import uuid
import asyncpg

from app.core.config import settings
from app.domain.extraction import MemoryExtractor, ExtractionError
from app.domain.apply import MemoryApplier
from app.domain.ports import EmbeddingPort

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
    Executes single-pass extraction pipeline for an event:
    1. Resolves event content, context_id, and timestamps.
    2. Runs structured LLM extraction with dead-letter staging on failure.
    3. Applies extraction (entity resolution, evidence verification, non-destructive versioning).
    4. Computes embeddings with task prefix 'search_document'.
    """
    content = content_override
    context_id = None
    event_occurred_at = None

    async with pool.acquire() as conn:
        ev_row = await conn.fetchrow(
            "SELECT content, context_id, occurred_at FROM events WHERE id = $1",
            event_id,
        )
        if ev_row:
            if not content:
                content = ev_row["content"]
            context_id = ev_row["context_id"]
            event_occurred_at = ev_row["occurred_at"]
        elif not content:
            raise ValueError(f"Event {event_id} not found and no content provided")

        if not context_id:
            ctx_row = await conn.fetchrow(
                "SELECT id FROM contexts WHERE slug = $1",
                context_slug,
            )
            context_id = ctx_row["id"] if ctx_row else None

    if not context_id:
        raise ValueError(f"Could not resolve context: {context_slug}")

    # Determine captured_at timestamp string
    if captured_at:
        captured_at_str = captured_at
    elif event_occurred_at:
        captured_at_str = event_occurred_at.isoformat()
    else:
        captured_at_str = datetime.now(timezone.utc).isoformat()

    # 2. Run structured LLM extraction with dead-letter staging on failure
    extractor = extractor or MemoryExtractor()
    try:
        extraction_data = await extractor.extract(
            text=content,
            speaker=speaker,
            captured_at=captured_at_str,
            tz=tz,
        )
    except ExtractionError as exc:
        logger.warning(f"Extraction failed for event {event_id} [{exc.kind}]: {exc.detail}")
        async with pool.acquire() as conn:
            async with conn.transaction():
                # a. Record extraction attempt
                await conn.execute(
                    """
                    INSERT INTO extraction_attempts (
                        event_id, model, attempt, ok, error_kind, error_detail, raw_output, latency_ms
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    event_id,
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
                    event_id,
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
                        "event_id": str(event_id),
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
                    event_id,
                    exc.detail,
                )

        raise exc

    # 3. Apply extraction (entity resolution, evidence verification, non-destructive versioning)
    applier = MemoryApplier(pool)
    apply_result = await applier.apply_extraction(
        event_id=event_id,
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
            event_id,
            extractor.model,
            1,
            True,
            0,
        )

    # 4. Generate & store embeddings for created memories (if embedding adapter is available)
    if embedding_adapter and apply_result.get("memories"):
        async with pool.acquire() as conn:
            for m in apply_result["memories"]:
                try:
                    stmt = m.get("statement", "")
                    if stmt:
                        vec = await embedding_adapter.generate_embedding(stmt, prefix="search_document")
                        model_name = getattr(embedding_adapter, "_model", None) or getattr(embedding_adapter, "model", settings.EMBEDDING_MODEL)
                        await conn.execute(
                            "UPDATE memories SET embedding = $1, embedding_model = $2 WHERE id = $3",
                            list(vec),
                            model_name,
                            uuid.UUID(m["id"]),
                        )
                except Exception as e:
                    logger.warning(f"Failed to embed memory {m.get('id')}: {e}")

    return apply_result
