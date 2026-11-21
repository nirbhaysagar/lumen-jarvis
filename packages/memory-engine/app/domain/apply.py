"""
Storage and application logic for extracted memories, entities, citations, and relations.
Implements non-destructive versioning, verbatim evidence linking, and conflict resolution.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid
import asyncpg

from app.domain.evidence import find_evidence_span

logger = logging.getLogger("lumen.memory.apply")


class MemoryApplier:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_or_create_entity(
        self,
        conn: asyncpg.Connection,
        name: str,
        kind: str,
        aliases: list[str],
        event_id: uuid.UUID,
        context_id: uuid.UUID,
    ) -> uuid.UUID:
        """Finds existing entity by canonical name or alias, or inserts a new entity."""
        clean_name = name.strip()
        # 1. Exact match by canonical_name and kind
        row = await conn.fetchrow(
            "SELECT id FROM entities WHERE lower(canonical_name) = lower($1) AND kind = $2",
            clean_name,
            kind,
        )
        if row:
            return row["id"]

        # 2. Check aliases
        alias_row = await conn.fetchrow(
            "SELECT entity_id FROM entity_aliases WHERE lower(alias) = lower($1)",
            clean_name,
        )
        if alias_row:
            return alias_row["entity_id"]

        # 3. Create new entity
        new_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO entities (id, canonical_name, kind, context_id, created_event)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (canonical_name, kind) DO UPDATE SET canonical_name = EXCLUDED.canonical_name
            RETURNING id
            """,
            new_id,
            clean_name,
            kind,
            context_id,
            event_id,
        )
        row = await conn.fetchrow(
            "SELECT id FROM entities WHERE lower(canonical_name) = lower($1) AND kind = $2",
            clean_name,
            kind,
        )
        ent_id = row["id"] if row else new_id

        # Insert aliases
        for alias in aliases:
            c_alias = alias.strip()
            if c_alias and c_alias.lower() != clean_name.lower():
                await conn.execute(
                    "INSERT INTO entity_aliases (entity_id, alias) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    ent_id,
                    c_alias,
                )

        return ent_id

    async def apply_extraction(
        self,
        event_id: uuid.UUID,
        context_id: uuid.UUID,
        raw_text: str,
        extraction_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Applies a validated extraction output:
        1. Resolves/creates entities.
        2. Verifies evidence spans against raw_text.
        3. Applies conflict/versioning rules (DUPLICATE vs UPDATE vs COMPATIBLE).
        4. Inserts memories, citations, and relations.
        """
        entity_ref_to_id: dict[str, uuid.UUID] = {}
        memory_ref_to_id: dict[str, uuid.UUID] = {}
        applied_memories: list[dict[str, Any]] = []

        entities = extraction_data.get("entities", [])
        memories = extraction_data.get("memories", [])

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Resolve entities
                for ent in entities:
                    ref = ent.get("ref")
                    name = ent.get("name")
                    kind = ent.get("kind", "other")
                    aliases = ent.get("aliases", [])
                    if ref and name:
                        e_id = await self.get_or_create_entity(
                            conn, name, kind, aliases, event_id, context_id
                        )
                        entity_ref_to_id[ref] = e_id

                # 2. Process memories
                for mem in memories:
                    m_ref = mem.get("ref")
                    m_type = mem.get("type", "fact")
                    epistemic = mem.get("epistemic", "fact")
                    asserted_by = mem.get("asserted_by", "user")
                    statement = mem.get("statement", "").strip()
                    predicate = mem.get("predicate")
                    cardinality = mem.get("cardinality", "many")
                    polarity = mem.get("polarity", True)
                    confidence = float(mem.get("confidence", 0.9))
                    importance = float(mem.get("importance", 0.5))
                    change_cue = mem.get("change_cue", False)

                    sub_ref = mem.get("subject")
                    obj_ref = mem.get("object")
                    obj_val = mem.get("object_value")

                    subject_id = entity_ref_to_id.get(sub_ref) if sub_ref else None
                    object_id = entity_ref_to_id.get(obj_ref) if obj_ref else None

                    # Check evidence quotes
                    evidence_quotes = mem.get("evidence", [])
                    best_span = (0, len(raw_text), False)
                    primary_quote = raw_text
                    if evidence_quotes:
                        primary_quote = evidence_quotes[0]
                        c_start, c_end, is_verified = find_evidence_span(raw_text, primary_quote)
                        if is_verified:
                            best_span = (c_start, c_end, True)
                        else:
                            # Try remaining quotes
                            for q in evidence_quotes[1:]:
                                cs, ce, ok = find_evidence_span(raw_text, q)
                                if ok:
                                    best_span = (cs, ce, True)
                                    primary_quote = q
                                    break

                    span_start, span_end, is_verified = best_span
                    if not is_verified:
                        # Downgrade unverified claims
                        confidence = confidence * 0.5
                        status = "pending_review"
                    else:
                        status = "active"

                    # 3. Conflict / Versioning Check
                    # Check current memories with same subject and predicate in this context
                    existing = None
                    if subject_id and predicate:
                        existing = await conn.fetchrow(
                            """
                            SELECT id, lineage_id, version, statement, object_id, object_value,
                                   confidence, status, cardinality
                            FROM memories
                            WHERE subject_id = $1 AND predicate = $2 AND context_id = $3
                              AND superseded_at IS NULL AND status = 'active'
                            ORDER BY version DESC LIMIT 1
                            """,
                            subject_id,
                            predicate,
                            context_id,
                        )

                    now = datetime.now(timezone.utc)
                    memory_id = uuid.uuid4()

                    if existing and (cardinality == "one" or change_cue):
                        # UPDATE: Supersede prior version
                        old_id = existing["id"]
                        lineage_id = existing["lineage_id"]
                        new_version = existing["version"] + 1

                        # 1. Insert new version in same lineage first (so FK superseded_by is satisfied)
                        await conn.execute(
                            """
                            INSERT INTO memories (
                                id, lineage_id, version, type, epistemic, asserted_by,
                                statement, subject_id, predicate, object_id, object_value,
                                cardinality, polarity, confidence, importance, context_id,
                                status, created_event, recorded_at
                            )
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
                            """,
                            memory_id,
                            lineage_id,
                            new_version,
                            m_type,
                            epistemic,
                            asserted_by,
                            statement,
                            subject_id,
                            predicate,
                            object_id,
                            json.dumps(obj_val) if obj_val is not None else None,
                            cardinality,
                            polarity,
                            confidence,
                            importance,
                            context_id,
                            status,
                            event_id,
                            now,
                        )

                        # 2. Close out old memory (close-out trigger permits setting superseded_at, superseded_by, valid_to once)
                        await conn.execute(
                            """
                            UPDATE memories
                            SET superseded_at = $1, superseded_by = $2, valid_to = $1
                            WHERE id = $3
                            """,
                            now,
                            memory_id,
                            old_id,
                        )

                        # 3. Add SUPERSEDES relation
                        rel_id = uuid.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO memory_relations (id, type, from_memory, to_memory, confidence, created_event)
                            VALUES ($1, 'SUPERSEDES', $2, $3, 1.0, $4)
                            """,
                            rel_id,
                            memory_id,
                            old_id,
                            event_id,
                        )

                    else:
                        # New lineage
                        lineage_id = uuid.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO memories (
                                id, lineage_id, version, type, epistemic, asserted_by,
                                statement, subject_id, predicate, object_id, object_value,
                                cardinality, polarity, confidence, importance, context_id,
                                status, created_event, recorded_at
                            )
                            VALUES ($1, $2, 1, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
                            """,
                            memory_id,
                            lineage_id,
                            m_type,
                            epistemic,
                            asserted_by,
                            statement,
                            subject_id,
                            predicate,
                            object_id,
                            json.dumps(obj_val) if obj_val is not None else None,
                            cardinality,
                            polarity,
                            confidence,
                            importance,
                            context_id,
                            status,
                            event_id,
                            now,
                        )

                        # Initialize memory_signals
                        await conn.execute(
                            """
                            INSERT INTO memory_signals (lineage_id, access_count, reaffirm_count, last_accessed_at, stability_days)
                            VALUES ($1, 0, 0, $2, 7.0)
                            ON CONFLICT (lineage_id) DO NOTHING
                            """,
                            lineage_id,
                            now,
                        )

                    if m_ref:
                        memory_ref_to_id[m_ref] = memory_id

                    # 4. Insert Citations
                    cit_id = uuid.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO citations (id, memory_id, event_id, char_start, char_end, quote, verified, role)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, 'evidence')
                        """,
                        cit_id,
                        memory_id,
                        event_id,
                        span_start,
                        span_end,
                        primary_quote,
                        is_verified,
                    )

                    # 5. Insert MENTIONS relations for subject and object
                    if subject_id:
                        await conn.execute(
                            """
                            INSERT INTO memory_relations (id, type, from_memory, to_entity, confidence, created_event)
                            VALUES ($1, 'MENTIONS', $2, $3, 1.0, $4)
                            """,
                            uuid.uuid4(),
                            memory_id,
                            subject_id,
                            event_id,
                        )
                    if object_id:
                        await conn.execute(
                            """
                            INSERT INTO memory_relations (id, type, from_memory, to_entity, confidence, created_event)
                            VALUES ($1, 'MENTIONS', $2, $3, 1.0, $4)
                            """,
                            uuid.uuid4(),
                            memory_id,
                            object_id,
                            event_id,
                        )

                    applied_memories.append({
                        "id": str(memory_id),
                        "lineage_id": str(lineage_id),
                        "statement": statement,
                        "type": m_type,
                        "epistemic": epistemic,
                        "verified": is_verified,
                    })

                # 6. Inter-memory relations
                for mem in memories:
                    m_ref = mem.get("ref")
                    from_id = memory_ref_to_id.get(m_ref)
                    if not from_id:
                        continue
                    for rel in mem.get("relations", []):
                        r_type = rel.get("type")
                        to_ref = rel.get("to")
                        to_id = memory_ref_to_id.get(to_ref)
                        if to_id and r_type in {"SUPPORTS", "CONTRADICTS", "DEPENDS_ON", "CAUSED_BY", "PART_OF", "RELATED_TO"}:
                            await conn.execute(
                                """
                                INSERT INTO memory_relations (id, type, from_memory, to_memory, confidence, created_event)
                                VALUES ($1, $2, $3, $4, 1.0, $5)
                                """,
                                uuid.uuid4(),
                                r_type,
                                from_id,
                                to_id,
                                event_id,
                            )

        return {
            "event_id": str(event_id),
            "memories_created": len(applied_memories),
            "memories": applied_memories,
        }
