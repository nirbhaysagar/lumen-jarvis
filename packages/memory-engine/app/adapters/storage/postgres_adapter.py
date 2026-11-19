"""
PostgreSQL + pgvector Storage Adapter.
Implements the StoragePort interface using native asyncpg connection pooling.
"""
from __future__ import annotations
import json
import uuid
from typing import Sequence
import asyncpg
from pgvector.asyncpg import register_vector

from app.domain.models import MemoryEvent, CanonicalFact, MemorySearchResult, MemoryType, EpistemicType
from app.domain.ports import StoragePort


class PostgresStorageAdapter(StoragePort):
    def __init__(self, dsn: str, pool_min: int = 2, pool_max: int = 10):
        self._dsn = dsn
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Establish asyncpg connection pool and register pgvector types."""
        async def init_connection(conn: asyncpg.Connection):
            await register_vector(conn)

        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._pool_min,
            max_size=self._pool_max,
            init=init_connection,
        )

    async def disconnect(self) -> None:
        """Gracefully close the connection pool."""
        if self._pool:
            await self._pool.close()

    async def initialize_schema(self) -> None:
        """Ensure pgvector extension, tables, and HNSW indexes exist."""
        if not self._pool:
            raise RuntimeError("Database pool not connected. Call connect() first.")

        async with self._pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            await conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

            # 1. Raw Immutable Event Ledger (L0)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    idempotency_key TEXT UNIQUE,
                    source TEXT NOT NULL,
                    conversation_id TEXT,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_conv ON events (conversation_id);
                CREATE INDEX IF NOT EXISTS idx_events_created ON events (created_at DESC);
            """)

            # 2. Canonical Derived Facts (L1)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    entity TEXT NOT NULL,
                    attribute TEXT NOT NULL,
                    value JSONB NOT NULL,
                    memory_type TEXT NOT NULL,
                    epistemic_type TEXT NOT NULL,
                    confidence FLOAT NOT NULL DEFAULT 1.0,
                    source_event_ids UUID[] NOT NULL DEFAULT '{}',
                    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    valid_to TIMESTAMPTZ,
                    embedding vector(1536) NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts (entity);
                CREATE INDEX IF NOT EXISTS idx_facts_type ON facts (memory_type);
                CREATE INDEX IF NOT EXISTS idx_facts_embedding_hnsw 
                    ON facts USING hnsw (embedding vector_cosine_ops);
            """)

    async def save_event(self, event: MemoryEvent) -> uuid.UUID:
        """Save raw event idempotently. If idempotency_key exists, returns existing id."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        payload_json = json.dumps(event.payload)

        async with self._pool.acquire() as conn:
            if event.idempotency_key:
                row = await conn.fetchrow(
                    "SELECT id FROM events WHERE idempotency_key = $1",
                    event.idempotency_key,
                )
                if row:
                    return row["id"]

            row = await conn.fetchrow(
                """
                INSERT INTO events (id, idempotency_key, source, conversation_id, payload, created_at, hash)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
                RETURNING id;
                """,
                event.id,
                event.idempotency_key,
                event.source,
                event.conversation_id,
                payload_json,
                event.created_at,
                event.hash,
            )
            return row["id"]

    async def save_fact(self, fact: CanonicalFact, embedding: Sequence[float]) -> uuid.UUID:
        """Store canonical fact with vector embedding."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        value_json = json.dumps(fact.value)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO facts (
                    id, entity, attribute, value, memory_type, epistemic_type, 
                    confidence, source_event_ids, valid_from, valid_to, embedding
                )
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id;
                """,
                fact.id,
                fact.entity,
                fact.attribute,
                value_json,
                fact.memory_type.value,
                fact.epistemic_type.value,
                fact.confidence,
                fact.source_event_ids,
                fact.valid_from,
                fact.valid_to,
                embedding,
            )
            return row["id"]

    async def search_facts(
        self,
        query_embedding: Sequence[float],
        limit: int = 5,
        threshold: float = 0.5,
        memory_types: Sequence[MemoryType] | None = None,
    ) -> list[MemorySearchResult]:
        """Perform HNSW cosine vector search in PostgreSQL."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        type_filters = [t.value for t in memory_types] if memory_types else None

        query = """
            SELECT 
                id, entity, attribute, value, memory_type, epistemic_type,
                confidence, source_event_ids, valid_from, valid_to,
                1 - (embedding <=> $1) AS similarity
            FROM facts
            WHERE (valid_to IS NULL OR valid_to > NOW())
              AND ($3::text[] IS NULL OR memory_type = ANY($3::text[]))
              AND 1 - (embedding <=> $1) >= $4
            ORDER BY embedding <=> $1
            LIMIT $2;
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, query_embedding, limit, type_filters, threshold)
            results = []
            for r in rows:
                val = r["value"]
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except Exception:
                        pass

                fact = CanonicalFact(
                    id=r["id"],
                    entity=r["entity"],
                    attribute=r["attribute"],
                    value=val,
                    memory_type=MemoryType(r["memory_type"]),
                    epistemic_type=EpistemicType(r["epistemic_type"]),
                    confidence=r["confidence"],
                    source_event_ids=list(r["source_event_ids"]),
                    valid_from=r["valid_from"],
                    valid_to=r["valid_to"],
                )
                results.append(MemorySearchResult(
                    fact=fact,
                    similarity=float(r["similarity"]),
                ))
            return results

    async def get_fact_trace(self, fact_id: uuid.UUID) -> dict | None:
        """Retrieve fact and its full provenance trace back to source events."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        async with self._pool.acquire() as conn:
            fact_row = await conn.fetchrow("SELECT * FROM facts WHERE id = $1", fact_id)
            if not fact_row:
                return None

            source_event_ids = list(fact_row["source_event_ids"])
            event_rows = []
            if source_event_ids:
                event_rows = await conn.fetch(
                    "SELECT id, source, conversation_id, payload, created_at, hash FROM events WHERE id = ANY($1)",
                    source_event_ids,
                )

            val = fact_row["value"]
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except Exception:
                    pass

            return {
                "fact": {
                    "id": str(fact_row["id"]),
                    "entity": fact_row["entity"],
                    "attribute": fact_row["attribute"],
                    "value": val,
                    "memory_type": fact_row["memory_type"],
                    "epistemic_type": fact_row["epistemic_type"],
                    "confidence": fact_row["confidence"],
                    "valid_from": fact_row["valid_from"].isoformat(),
                    "valid_to": fact_row["valid_to"].isoformat() if fact_row["valid_to"] else None,
                    "source_event_ids": [str(sid) for sid in source_event_ids],
                },
                "source_events": [
                    {
                        "id": str(e["id"]),
                        "source": e["source"],
                        "conversation_id": e["conversation_id"],
                        "payload": json.loads(e["payload"]) if isinstance(e["payload"], str) else e["payload"],
                        "created_at": e["created_at"].isoformat(),
                        "hash": e["hash"],
                    }
                    for e in event_rows
                ],
            }
