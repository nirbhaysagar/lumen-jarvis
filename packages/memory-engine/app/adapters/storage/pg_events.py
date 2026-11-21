"""
PostgreSQL Storage Adapter for L0 Append-Only Events and Capture.
Enforces single-transaction atomicity:
source upsert -> event append -> chunk slicing -> durable job enqueuing.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any
import uuid
import asyncpg

from app.domain.events import CaptureRequest, CaptureResult

logger = logging.getLogger("lumen.memory.events")


class PostgresEventsAdapter:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_or_create_context(self, conn: asyncpg.Connection, slug: str) -> uuid.UUID:
        row = await conn.fetchrow("SELECT id FROM contexts WHERE slug = $1", slug)
        if row:
            return row["id"]
        ctx_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO contexts (id, slug, name) VALUES ($1, $2, $3) ON CONFLICT (slug) DO NOTHING",
            ctx_id,
            slug,
            slug.capitalize(),
        )
        row = await conn.fetchrow("SELECT id FROM contexts WHERE slug = $1", slug)
        return row["id"]

    async def capture(self, req: CaptureRequest) -> CaptureResult:
        """
        Synchronously captures raw text into L0 in <20ms without invoking an LLM.
        All operations run in one database transaction.
        """
        normalized_content = req.content.strip()
        content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
        occurred_at = req.occurred_at or datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Resolve context
                context_id = await self.get_or_create_context(conn, req.context)

                # 2. Check Idempotency Key
                if req.idempotency_key:
                    existing = await conn.fetchrow(
                        "SELECT id, source_id, context_id FROM events WHERE idempotency_key = $1",
                        req.idempotency_key,
                    )
                    if existing:
                        # Existing event: fetch existing jobs
                        job_rows = await conn.fetch(
                            "SELECT id FROM jobs WHERE event_id = $1 ORDER BY id ASC",
                            existing["id"],
                        )
                        return CaptureResult(
                            event_id=existing["id"],
                            job_ids=[r["id"] for r in job_rows],
                            idempotent_hit=True,
                            context_id=existing["context_id"],
                            source_id=existing["source_id"],
                        )

                # 3. Upsert source by content_hash
                source_id = uuid.uuid4()
                source_row = await conn.fetchrow(
                    """
                    INSERT INTO sources (id, kind, uri, title, author, content_hash, published_at, context_id, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (content_hash) DO UPDATE
                        SET title = COALESCE(EXCLUDED.title, sources.title)
                    RETURNING id
                    """,
                    source_id,
                    req.source.kind,
                    req.source.uri,
                    req.source.title,
                    req.source.author,
                    content_hash,
                    occurred_at,
                    context_id,
                    json.dumps(req.source.metadata),
                )
                source_id = source_row["id"]

                # 4. Insert CAPTURE event (append-only)
                event_id = uuid.uuid4()
                event_type = f"capture.{req.source.kind}"
                actor = req.source.author or "user"
                await conn.execute(
                    """
                    INSERT INTO events (
                        id, kind, type, actor, source_id, context_id,
                        content, content_hash, idempotency_key, occurred_at
                    )
                    VALUES ($1, 'CAPTURE', $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    event_id,
                    event_type,
                    actor,
                    source_id,
                    context_id,
                    normalized_content,
                    content_hash,
                    req.idempotency_key,
                    occurred_at,
                )

                # 5. Split chunks (character spans)
                # For short texts (< 1000 chars), single chunk; for longer, 1000 char windows with 200 overlap
                chunks_to_insert = []
                chunk_size = 1000
                chunk_overlap = 200
                text_len = len(normalized_content)

                if text_len <= chunk_size:
                    chunks_to_insert.append((0, 0, text_len, normalized_content))
                else:
                    ord_num = 0
                    start = 0
                    while start < text_len:
                        end = min(start + chunk_size, text_len)
                        chunk_text = normalized_content[start:end]
                        chunks_to_insert.append((ord_num, start, end, chunk_text))
                        if end == text_len:
                            break
                        start += (chunk_size - chunk_overlap)
                        ord_num += 1

                for ord_num, c_start, c_end, c_text in chunks_to_insert:
                    chunk_id = uuid.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO chunks (id, event_id, ord, char_start, char_end, text)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        chunk_id,
                        event_id,
                        ord_num,
                        c_start,
                        c_end,
                        c_text,
                    )

                # 6. Enqueue durable jobs: embed and extract
                job_ids = []
                for job_kind in ("embed", "extract"):
                    row = await conn.fetchrow(
                        """
                        INSERT INTO jobs (kind, event_id, payload, status)
                        VALUES ($1, $2, '{}'::jsonb, 'queued')
                        ON CONFLICT (kind, event_id) DO UPDATE SET attempts = jobs.attempts
                        RETURNING id
                        """,
                        job_kind,
                        event_id,
                    )
                    if row:
                        job_ids.append(row["id"])

                return CaptureResult(
                    event_id=event_id,
                    job_ids=job_ids,
                    idempotent_hit=False,
                    context_id=context_id,
                    source_id=source_id,
                )
