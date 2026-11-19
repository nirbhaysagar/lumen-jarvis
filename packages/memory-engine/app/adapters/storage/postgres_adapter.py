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

from app.domain.models import (
    MemoryEvent,
    CanonicalFact,
    MemorySearchResult,
    MemoryType,
    EpistemicType,
    Entity,
    Triple,
    ReviewProposal,
    ProposalStatus,
    ProposalTier,
)
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
        """Store canonical fact with vector embedding, automatically superseding previous active versions."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        value_json = json.dumps(fact.value)

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # 1. Deduplication Guard: If an active fact with identical value already exists, return it
                existing_row = await conn.fetchrow(
                    """
                    SELECT id, value FROM facts
                    WHERE entity = $1 AND attribute = $2 AND valid_to IS NULL;
                    """,
                    fact.entity,
                    fact.attribute,
                )
                if existing_row:
                    ex_val = existing_row["value"]
                    if isinstance(ex_val, str):
                        try:
                            ex_val = json.loads(ex_val)
                        except Exception:
                            pass
                    if ex_val == fact.value:
                        # Provenance Reaffirmation: Merge any new source_event_ids into existing fact DAG
                        if fact.source_event_ids:
                            await conn.execute(
                                """
                                UPDATE facts
                                SET source_event_ids = ARRAY(
                                    SELECT DISTINCT unnest(source_event_ids || $1::uuid[])
                                )
                                WHERE id = $2;
                                """,
                                fact.source_event_ids,
                                existing_row["id"],
                            )
                        return existing_row["id"]

                # 2. Automated Bi-Temporal Superseding:
                # If this fact is active (valid_to is None), retire previous active assertions for this entity/attribute.
                if fact.valid_to is None:
                    await conn.execute(
                        """
                        UPDATE facts
                        SET valid_to = $1
                        WHERE entity = $2
                          AND attribute = $3
                          AND (valid_to IS NULL OR valid_to > $1)
                          AND id != $4;
                        """,
                        fact.valid_from,
                        fact.entity,
                        fact.attribute,
                        fact.id,
                    )

                # 3. Insert the new canonical fact
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
        """Perform HNSW cosine vector search in PostgreSQL against currently valid facts."""
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

    async def search_facts_at_time(
        self,
        query_embedding: Sequence[float],
        as_of: datetime,
        limit: int = 5,
        threshold: float = 0.01,
        memory_types: Sequence[MemoryType] | None = None,
    ) -> list[MemorySearchResult]:
        """Perform bi-temporal time-travel cosine vector retrieval as of a specific point in time."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        type_filters = [t.value for t in memory_types] if memory_types else None

        query = """
            SELECT 
                id, entity, attribute, value, memory_type, epistemic_type,
                confidence, source_event_ids, valid_from, valid_to,
                1 - (embedding <=> $1) AS similarity
            FROM facts
            WHERE valid_from <= $2
              AND (valid_to IS NULL OR valid_to > $2)
              AND ($4::text[] IS NULL OR memory_type = ANY($4::text[]))
              AND 1 - (embedding <=> $1) >= $5
            ORDER BY embedding <=> $1
            LIMIT $3;
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, query_embedding, as_of, limit, type_filters, threshold)
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

    async def get_fact_trace(self, fact_id: str | uuid.UUID) -> dict | None:
        """Retrieve fact, its full provenance trace back to source events, and historical evolution lineage."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        fact_id_str = str(fact_id).strip()

        async with self._pool.acquire() as conn:
            # Flexible lookup: exact UUID or prefix matching
            try:
                fact_uuid = uuid.UUID(fact_id_str)
                fact_row = await conn.fetchrow("SELECT * FROM facts WHERE id = $1", fact_uuid)
            except ValueError:
                # Prefix lookup
                fact_row = await conn.fetchrow(
                    "SELECT * FROM facts WHERE id::text LIKE $1 ORDER BY valid_from DESC LIMIT 1",
                    f"{fact_id_str}%",
                )

            if not fact_row:
                return None

            source_event_ids = list(fact_row["source_event_ids"])
            event_rows = []
            if source_event_ids:
                event_rows = await conn.fetch(
                    "SELECT id, source, conversation_id, payload, created_at, hash FROM events WHERE id = ANY($1)",
                    source_event_ids,
                )

            # Historical lineage: all facts for the same entity and attribute
            lineage_rows = await conn.fetch(
                """
                SELECT id, entity, attribute, value, memory_type, epistemic_type, confidence, source_event_ids, valid_from, valid_to
                FROM facts
                WHERE entity = $1 AND attribute = $2
                ORDER BY valid_from ASC;
                """,
                fact_row["entity"],
                fact_row["attribute"],
            )

            val = fact_row["value"]
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except Exception:
                    pass

            def parse_val(v):
                if isinstance(v, str):
                    try:
                        return json.loads(v)
                    except Exception:
                        pass
                return v

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
                "history_lineage": [
                    {
                        "id": str(lr["id"]),
                        "entity": lr["entity"],
                        "attribute": lr["attribute"],
                        "value": parse_val(lr["value"]),
                        "memory_type": lr["memory_type"],
                        "epistemic_type": lr["epistemic_type"],
                        "confidence": lr["confidence"],
                        "source_event_ids": [str(sid) for sid in lr["source_event_ids"]],
                        "valid_from": lr["valid_from"].isoformat(),
                        "valid_to": lr["valid_to"].isoformat() if lr["valid_to"] else None,
                    }
                    for lr in lineage_rows
                ],
            }

    async def save_entity(self, entity: Entity, embedding: Sequence[float] | None = None) -> uuid.UUID:
        """Store or update a canonical entity node with its aliases and optional embedding."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        async with self._pool.acquire() as conn:
            vec_param = list(embedding) if embedding else None
            row = await conn.fetchrow(
                """
                INSERT INTO entities (id, canonical_name, category, aliases, description, embedding, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                ON CONFLICT (canonical_name) DO UPDATE SET
                    aliases = ARRAY(SELECT DISTINCT unnest(entities.aliases || EXCLUDED.aliases)),
                    category = COALESCE(EXCLUDED.category, entities.category),
                    description = COALESCE(EXCLUDED.description, entities.description),
                    embedding = COALESCE(EXCLUDED.embedding, entities.embedding),
                    metadata = entities.metadata || EXCLUDED.metadata,
                    updated_at = NOW()
                RETURNING id;
                """,
                entity.id,
                entity.canonical_name,
                entity.category,
                entity.aliases,
                entity.description,
                vec_param,
                json.dumps(entity.metadata),
                entity.created_at,
            )
            return row["id"]

    async def find_entity_by_name_or_alias(self, name_or_alias: str) -> Entity | None:
        """Lookup an entity by canonical name or alias match (case-insensitive)."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, canonical_name, category, aliases, description, metadata, created_at, updated_at
                FROM entities
                WHERE LOWER(canonical_name) = LOWER($1)
                   OR $1 = ANY(aliases)
                   OR LOWER($1) = ANY(SELECT LOWER(unnest(aliases)))
                LIMIT 1;
                """,
                name_or_alias.strip(),
            )
            if not row:
                return None
            return Entity(
                id=row["id"],
                canonical_name=row["canonical_name"],
                category=row["category"],
                aliases=list(row["aliases"]) if row["aliases"] else [],
                description=row["description"],
                metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else (row["metadata"] or {}),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    async def get_entity(self, entity_id: uuid.UUID) -> Entity | None:
        """Fetch entity by primary key ID."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, canonical_name, category, aliases, description, metadata, created_at, updated_at FROM entities WHERE id = $1",
                entity_id,
            )
            if not row:
                return None
            return Entity(
                id=row["id"],
                canonical_name=row["canonical_name"],
                category=row["category"],
                aliases=list(row["aliases"]) if row["aliases"] else [],
                description=row["description"],
                metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else (row["metadata"] or {}),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    async def save_triple(self, triple: Triple) -> uuid.UUID:
        """Persist a canonical relational triple with bi-temporal validity."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO triples (id, subject_id, predicate, object_id, confidence, source_event_ids, valid_from, valid_to, metadata, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id;
                """,
                triple.id,
                triple.subject_id,
                triple.predicate,
                triple.object_id,
                triple.confidence,
                triple.source_event_ids,
                triple.valid_from,
                triple.valid_to,
                json.dumps(triple.metadata),
                triple.created_at,
            )
            return row["id"]

    async def get_active_triples_for_subject(self, subject_id: uuid.UUID, predicate: str | None = None) -> list[Triple]:
        """Fetch all currently active triples for a subject entity (valid_to IS NULL)."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, subject_id, predicate, object_id, confidence, source_event_ids, valid_from, valid_to, metadata, created_at
                FROM triples
                WHERE subject_id = $1 AND valid_to IS NULL
                  AND ($2::text IS NULL OR predicate = $2);
                """,
                subject_id,
                predicate,
            )
            return [
                Triple(
                    id=r["id"],
                    subject_id=r["subject_id"],
                    predicate=r["predicate"],
                    object_id=r["object_id"],
                    confidence=r["confidence"],
                    source_event_ids=list(r["source_event_ids"]) if r["source_event_ids"] else [],
                    valid_from=r["valid_from"],
                    valid_to=r["valid_to"],
                    metadata=json.loads(r["metadata"]) if isinstance(r["metadata"], str) else (r["metadata"] or {}),
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    async def stage_review_proposal(self, proposal: ReviewProposal) -> uuid.UUID:
        """Insert a proposed triple or entity change into the Review Queue staging table."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO review_queue (
                    id, proposal_type, subject_raw, predicate_raw, object_raw,
                    resolved_subject_id, resolved_predicate, resolved_object_id,
                    confidence, tier, status, conflict_reason, conflicting_triple_id,
                    source_event_id, created_at, reviewed_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                RETURNING id;
                """,
                proposal.id,
                proposal.proposal_type,
                proposal.subject_raw,
                proposal.predicate_raw,
                proposal.object_raw,
                proposal.resolved_subject_id,
                proposal.resolved_predicate,
                proposal.resolved_object_id,
                proposal.confidence,
                proposal.tier.value if hasattr(proposal.tier, "value") else str(proposal.tier),
                proposal.status.value if hasattr(proposal.status, "value") else str(proposal.status),
                proposal.conflict_reason,
                proposal.conflicting_triple_id,
                proposal.source_event_id,
                proposal.created_at,
                proposal.reviewed_at,
            )
            return row["id"]

    async def list_review_proposals(
        self,
        status: ProposalStatus | None = None,
        tier: ProposalTier | None = None,
        limit: int = 50,
    ) -> list[ReviewProposal]:
        """List staged proposals from the review queue filtered by status and tier."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        status_val = status.value if hasattr(status, "value") else status
        tier_val = tier.value if hasattr(tier, "value") else tier

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, proposal_type, subject_raw, predicate_raw, object_raw,
                       resolved_subject_id, resolved_predicate, resolved_object_id,
                       confidence, tier, status, conflict_reason, conflicting_triple_id,
                       source_event_id, created_at, reviewed_at
                FROM review_queue
                WHERE ($1::text IS NULL OR status = $1)
                  AND ($2::text IS NULL OR tier = $2)
                ORDER BY created_at DESC
                LIMIT $3;
                """,
                status_val,
                tier_val,
                limit,
            )
            return [
                ReviewProposal(
                    id=r["id"],
                    proposal_type=r["proposal_type"],
                    subject_raw=r["subject_raw"],
                    predicate_raw=r["predicate_raw"],
                    object_raw=r["object_raw"],
                    resolved_subject_id=r["resolved_subject_id"],
                    resolved_predicate=r["resolved_predicate"],
                    resolved_object_id=r["resolved_object_id"],
                    confidence=r["confidence"],
                    tier=ProposalTier(r["tier"]),
                    status=ProposalStatus(r["status"]),
                    conflict_reason=r["conflict_reason"],
                    conflicting_triple_id=r["conflicting_triple_id"],
                    source_event_id=r["source_event_id"],
                    created_at=r["created_at"],
                    reviewed_at=r["reviewed_at"],
                )
                for r in rows
            ]

    async def resolve_review_proposal(
        self,
        proposal_id: uuid.UUID,
        decision: ProposalStatus,
    ) -> bool:
        """Approve or reject a staged proposal in the review queue."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        decision_val = decision.value if hasattr(decision, "value") else str(decision)

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                prop = await conn.fetchrow("SELECT * FROM review_queue WHERE id = $1 FOR UPDATE;", proposal_id)
                if not prop:
                    return False

                if decision == ProposalStatus.APPROVED:
                    # If this was a contradiction update, retire old conflicting triple
                    if prop["conflicting_triple_id"]:
                        await conn.execute(
                            "UPDATE triples SET valid_to = NOW() WHERE id = $1 AND valid_to IS NULL;",
                            prop["conflicting_triple_id"],
                        )

                    # Insert new canonical triple
                    if prop["resolved_subject_id"] and prop["resolved_predicate"] and prop["resolved_object_id"]:
                        await conn.execute(
                            """
                            INSERT INTO triples (subject_id, predicate, object_id, confidence, source_event_ids, valid_from)
                            VALUES ($1, $2, $3, $4, ARRAY[$5::uuid], NOW());
                            """,
                            prop["resolved_subject_id"],
                            prop["resolved_predicate"],
                            prop["resolved_object_id"],
                            prop["confidence"],
                            prop["source_event_id"],
                        )

                # Update review queue row
                await conn.execute(
                    "UPDATE review_queue SET status = $1, reviewed_at = NOW() WHERE id = $2;",
                    decision_val,
                    proposal_id,
                )
                return True

    async def traverse_graph(
        self,
        start_entity_id: uuid.UUID,
        max_depth: int = 2,
    ) -> list[dict]:
        """Traverse connected entities and triples up to max_depth using recursive SQL."""
        if not self._pool:
            raise RuntimeError("Database pool not connected.")

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH RECURSIVE graph_walk AS (
                    SELECT t.id as triple_id, t.subject_id, t.predicate, t.object_id,
                           e_sub.canonical_name as subject_name,
                           e_obj.canonical_name as object_name,
                           1 as depth,
                           ARRAY[t.id] as path
                    FROM triples t
                    JOIN entities e_sub ON t.subject_id = e_sub.id
                    JOIN entities e_obj ON t.object_id = e_obj.id
                    WHERE (t.subject_id = $1 OR t.object_id = $1)
                      AND t.valid_to IS NULL

                    UNION ALL

                    SELECT t.id as triple_id, t.subject_id, t.predicate, t.object_id,
                           e_sub.canonical_name as subject_name,
                           e_obj.canonical_name as object_name,
                           gw.depth + 1,
                           gw.path || t.id
                    FROM triples t
                    JOIN graph_walk gw ON (t.subject_id = gw.object_id OR t.object_id = gw.subject_id)
                    JOIN entities e_sub ON t.subject_id = e_sub.id
                    JOIN entities e_obj ON t.object_id = e_obj.id
                    WHERE gw.depth < $2
                      AND t.valid_to IS NULL
                      AND NOT (t.id = ANY(gw.path))
                )
                SELECT DISTINCT triple_id, subject_id, predicate, object_id, subject_name, object_name, depth
                FROM graph_walk
                ORDER BY depth ASC;
                """,
                start_entity_id,
                max_depth,
            )
            return [
                {
                    "triple_id": str(r["triple_id"]),
                    "subject_id": str(r["subject_id"]),
                    "predicate": r["predicate"],
                    "object_id": str(r["object_id"]),
                    "subject_name": r["subject_name"],
                    "object_name": r["object_name"],
                    "depth": r["depth"],
                }
                for r in rows
            ]

