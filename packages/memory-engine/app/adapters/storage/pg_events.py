"""
PostgreSQL Storage Adapter for L0 Append-Only Events and Capture.
Enforces single-transaction atomicity:
source_key upsert -> hash chain -> event append -> paragraph chunking -> durable job enqueuing.
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
from app.domain.sensitivity import scan
from app.pipeline.chunker import ParagraphChunker

logger = logging.getLogger("lumen.memory.events")


class PostgresEventsAdapter:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.chunker = ParagraphChunker(budget=1000, overlap_sentences=1)

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
        All operations run in one database transaction with a tamper-evident hash chain.
        """
        normalized_content = req.content.strip()
        content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
        occurred_at = req.occurred_at or datetime.now(timezone.utc)
        speaker = req.speaker or req.source.author or "user"
        sensitivity_hint = scan(normalized_content)

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
                            sensitivity_hint=sensitivity_hint,
                        )

                # 3. Source upsert via source_key (dedupes per author + uri + kind + content_hash)
                source_key = hashlib.sha256(
                    f"{req.source.kind}|{req.source.uri or ''}|{req.source.author or ''}|{content_hash}".encode("utf-8")
                ).hexdigest()

                source_id = uuid.uuid4()
                source_row = await conn.fetchrow(
                    """
                    INSERT INTO sources (id, kind, uri, title, author, content_hash, source_key, published_at, context_id, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (source_key) DO UPDATE
                        SET title = COALESCE(EXCLUDED.title, sources.title)
                    RETURNING id
                    """,
                    source_id,
                    req.source.kind,
                    req.source.uri,
                    req.source.title,
                    req.source.author,
                    content_hash,
                    source_key,
                    occurred_at,
                    context_id,
                    json.dumps(req.source.metadata or {}),
                )
                source_id = source_row["id"]

                # 4. Hash Chain (tamper-evident SHA-256 chain per context)
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext('chain:' || $1))", str(context_id))
                last_chain = await conn.fetchval(
                    "SELECT chain_hash FROM events WHERE context_id = $1 AND chain_hash IS NOT NULL ORDER BY recorded_at DESC, id DESC LIMIT 1",
                    context_id,
                )
                prev_hash = last_chain or ("0" * 64)
                seq_val = (await conn.fetchval("SELECT count(*) FROM events WHERE context_id = $1", context_id)) or 0
                chain_payload = f"{prev_hash}|{content_hash}|{occurred_at.isoformat()}|{speaker}|{seq_val}"
                chain_hash = hashlib.sha256(chain_payload.encode("utf-8")).hexdigest()

                # 5. Insert CAPTURE event (append-only)
                event_id = uuid.uuid4()
                event_type = f"capture.{req.source.kind}"
                actor = req.source.author or "user"
                recorded_at = req.recorded_at or datetime.now(timezone.utc)
                event_payload = json.dumps({"sensitivity_hint": sensitivity_hint})

                await conn.execute(
                    """
                    INSERT INTO events (
                        id, kind, type, actor, speaker, source_id, context_id,
                        content, content_hash, prev_hash, chain_hash, payload,
                        idempotency_key, occurred_at, recorded_at
                    )
                    VALUES ($1, 'CAPTURE', $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    """,
                    event_id,
                    event_type,
                    actor,
                    speaker,
                    source_id,
                    context_id,
                    normalized_content,
                    content_hash,
                    prev_hash,
                    chain_hash,
                    event_payload,
                    req.idempotency_key,
                    occurred_at,
                    recorded_at,
                )

                # 6. Paragraph-aware chunk slicing
                chunks = self.chunker.chunk(normalized_content)
                for chunk in chunks:
                    chunk_id = uuid.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO chunks (id, event_id, ord, char_start, char_end, text, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        chunk_id,
                        event_id,
                        chunk.ord,
                        chunk.char_start,
                        chunk.char_end,
                        chunk.text,
                        json.dumps(req.source.metadata or {}),
                    )

                # 7. Enqueue durable jobs: embed and extract
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
                    sensitivity_hint=sensitivity_hint,
                )
