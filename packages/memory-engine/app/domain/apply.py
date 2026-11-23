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
import dateparser

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

        # Outbox event for entity
        outbox_payload = {
            "entity_id": str(ent_id),
            "canonical_name": clean_name,
            "kind": kind,
            "aliases": aliases,
        }
        await conn.execute(
            """
            INSERT INTO outbox (event_id, topic, payload, status)
            VALUES ($1, 'entity.upserted', $2, 'queued')
            """,
            event_id,
            json.dumps(outbox_payload),
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
                # 0. Resolve source_id for provenance and claims
                event_row = await conn.fetchrow(
                    "SELECT source_id, actor, occurred_at, recorded_at FROM events WHERE id = $1",
                    event_id,
                )
                source_id = event_row["source_id"] if event_row else None
                event_actor = event_row["actor"] if event_row and event_row["actor"] else "user"
                event_occurred_at = event_row["occurred_at"] if event_row and event_row.get("occurred_at") else datetime.now(timezone.utc)
                if event_occurred_at.tzinfo is None:
                    event_occurred_at = event_occurred_at.replace(tzinfo=timezone.utc)
                event_recorded_at = event_row["recorded_at"] if event_row and event_row.get("recorded_at") else datetime.now(timezone.utc)
                if event_recorded_at.tzinfo is None:
                    event_recorded_at = event_recorded_at.replace(tzinfo=timezone.utc)

                if not source_id:
                    # Ensure a source exists for claims foreign key constraint
                    source_row = await conn.fetchrow(
                        """
                        INSERT INTO sources (id, kind, title, context_id, content_hash)
                        VALUES (gen_random_uuid(), 'chat', 'direct_event', $1, md5($2::text))
                        ON CONFLICT (content_hash) DO UPDATE SET title = EXCLUDED.title
                        RETURNING id
                        """,
                        context_id,
                        str(event_id),
                    )
                    source_id = source_row["id"]

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
                    asserted_by = mem.get("asserted_by") or event_actor or "user"
                    statement = mem.get("statement", "").strip()
                    predicate = mem.get("predicate")
                    cardinality = mem.get("cardinality", "many")
                    polarity = mem.get("polarity", True)
                    confidence = float(mem.get("confidence", 0.9))
                    importance = float(mem.get("importance", 0.5))
                    change_cue = mem.get("change_cue", False)

                    # Rule: A third party's word is a belief until confirmed
                    is_third_party = (asserted_by.lower() != "user")
                    if is_third_party and epistemic == "fact":
                        epistemic = "belief"

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
                    # Step 1: Look up existing memory scoped to own speaker
                    existing = None
                    if subject_id and predicate:
                        if cardinality == "one" or change_cue:
                            existing = await conn.fetchrow(
                                """
                                SELECT id, lineage_id, version, statement, object_id, object_value,
                                       confidence, status, cardinality, polarity
                                FROM memories
                                WHERE subject_id = $1 AND predicate = $2 AND context_id = $3
                                  AND asserted_by = $4 AND superseded_at IS NULL AND status = 'active'
                                ORDER BY version DESC LIMIT 1
                                """,
                                subject_id,
                                predicate,
                                context_id,
                                asserted_by,
                            )
                        elif polarity is False:
                            # Step 4: Polarity negation of an active positive memory from same speaker
                            existing = await conn.fetchrow(
                                """
                                SELECT id, lineage_id, version, statement, object_id, object_value,
                                       confidence, status, cardinality, polarity
                                FROM memories
                                WHERE subject_id = $1 AND predicate = $2 AND context_id = $3
                                  AND asserted_by = $4 AND superseded_at IS NULL AND status = 'active'
                                  AND polarity = true
                                  AND (
                                      ($5::uuid IS NOT NULL AND object_id = $5)
                                      OR ($6::text IS NOT NULL AND object_value = $6::jsonb)
                                      OR ($5::uuid IS NULL AND $6::text IS NULL)
                                  )
                                ORDER BY version DESC LIMIT 1
                                """,
                                subject_id,
                                predicate,
                                context_id,
                                asserted_by,
                                object_id,
                                json.dumps(obj_val) if obj_val is not None else None,
                            )

                    # Step 2: Bi-temporal anchor resolution (valid_from_text -> valid_from)
                    valid_from_text = mem.get("valid_from_text")
                    valid_from = None
                    if valid_from_text:
                        try:
                            import re
                            clean_text = re.sub(
                                r"^(since|from|as of|in|starting)\s+",
                                "",
                                valid_from_text.strip(),
                                flags=re.IGNORECASE,
                            )
                            parsed_dt = dateparser.parse(
                                clean_text,
                                settings={"RELATIVE_BASE": event_occurred_at, "RETURN_AS_TIMEZONE_AWARE": True},
                            )
                            if not parsed_dt:
                                parsed_dt = dateparser.parse(
                                    valid_from_text,
                                    settings={"RELATIVE_BASE": event_occurred_at, "RETURN_AS_TIMEZONE_AWARE": True},
                                )
                            if parsed_dt:
                                valid_from = parsed_dt
                        except Exception as e:
                            logger.warning(f"Failed to parse valid_from_text '{valid_from_text}': {e}")

                    if not valid_from:
                        if mem.get("valid_from"):
                            try:
                                valid_from = datetime.fromisoformat(mem["valid_from"])
                            except Exception:
                                valid_from = event_occurred_at
                        else:
                            valid_from = event_occurred_at

                    if valid_from and valid_from.tzinfo is None:
                        valid_from = valid_from.replace(tzinfo=timezone.utc)

                    now = event_recorded_at
                    memory_id = uuid.uuid4()

                    should_supersede = False
                    if existing:
                        if cardinality == "one" or change_cue:
                            should_supersede = True
                        elif polarity is False and existing["polarity"] is True:
                            should_supersede = True

                    if should_supersede and existing:
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
                                status, created_event, recorded_at, valid_from
                            )
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
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
                            valid_from,
                        )

                        # 2. Close out old memory: valid_to = new.valid_from (world time), superseded_at = now (tx time)
                        await conn.execute(
                            """
                            UPDATE memories
                            SET superseded_at = $1, superseded_by = $2, valid_to = $3
                            WHERE id = $4
                            """,
                            now,
                            memory_id,
                            valid_from,
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

                        # 4. Outbox events: memory.superseded & memory.created
                        outbox_super = {
                            "memory_id": str(old_id),
                            "lineage_id": str(lineage_id),
                            "version": existing["version"],
                            "superseded_by": str(memory_id),
                            "new_version": new_version,
                        }
                        super_ob_row = await conn.fetchrow(
                            """
                            INSERT INTO outbox (event_id, topic, payload, status)
                            VALUES ($1, 'memory.superseded', $2, 'queued')
                            RETURNING id
                            """,
                            event_id,
                            json.dumps(outbox_super),
                        )
                        if super_ob_row:
                            await conn.execute("SELECT pg_notify('lumen_outbox', $1)", str(super_ob_row["id"]))

                        # Append DERIVED event for superseded
                        await conn.execute(
                            """
                            INSERT INTO events (
                                id, kind, type, actor, context_id, caused_by, target_id, content, recorded_at, occurred_at
                            )
                            VALUES ($1, 'DERIVED', 'memory.superseded', 'system', $2, $3, $4, $5, $6, $7)
                            """,
                            uuid.uuid4(),
                            context_id,
                            [event_id],
                            old_id,
                            f"Superseded by {memory_id}",
                            now,
                            now,
                        )

                        outbox_created = {
                            "memory_id": str(memory_id),
                            "lineage_id": str(lineage_id),
                            "version": new_version,
                            "type": m_type,
                            "epistemic": epistemic,
                            "statement": statement,
                            "status": status,
                        }
                        create_ob_row = await conn.fetchrow(
                            """
                            INSERT INTO outbox (event_id, topic, payload, status)
                            VALUES ($1, 'memory.created', $2, 'queued')
                            RETURNING id
                            """,
                            event_id,
                            json.dumps(outbox_created),
                        )
                        if create_ob_row:
                            await conn.execute("SELECT pg_notify('lumen_outbox', $1)", str(create_ob_row["id"]))

                        # Append DERIVED event for created
                        await conn.execute(
                            """
                            INSERT INTO events (
                                id, kind, type, actor, context_id, caused_by, target_id, content, recorded_at, occurred_at
                            )
                            VALUES ($1, 'DERIVED', 'memory.created', 'system', $2, $3, $4, $5, $6, $7)
                            """,
                            uuid.uuid4(),
                            context_id,
                            [event_id],
                            memory_id,
                            statement,
                            now,
                            valid_from or now,
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
                                status, created_event, recorded_at, valid_from
                            )
                            VALUES ($1, $2, 1, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
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
                            valid_from,
                        )

                        # Outbox event for memory.created
                        outbox_created = {
                            "memory_id": str(memory_id),
                            "lineage_id": str(lineage_id),
                            "version": 1,
                            "type": m_type,
                            "epistemic": epistemic,
                            "statement": statement,
                            "status": status,
                        }
                        create_ob_row = await conn.fetchrow(
                            """
                            INSERT INTO outbox (event_id, topic, payload, status)
                            VALUES ($1, 'memory.created', $2, 'queued')
                            RETURNING id
                            """,
                            event_id,
                            json.dumps(outbox_created),
                        )
                        if create_ob_row:
                            await conn.execute("SELECT pg_notify('lumen_outbox', $1)", str(create_ob_row["id"]))

                        # Append DERIVED event for created
                        await conn.execute(
                            """
                            INSERT INTO events (
                                id, kind, type, actor, context_id, caused_by, target_id, content, recorded_at, occurred_at
                            )
                            VALUES ($1, 'DERIVED', 'memory.created', 'system', $2, $3, $4, $5, $6, $7)
                            """,
                            uuid.uuid4(),
                            context_id,
                            [event_id],
                            memory_id,
                            statement,
                            now,
                            valid_from or now,
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

                        # Step 2: If another speaker's active row on the same (subject, predicate) disagrees:
                        # Never supersede across speakers; add CONTRADICTS relation and review_queue row
                        if subject_id and predicate:
                            other_active = await conn.fetch(
                                """
                                SELECT id, lineage_id, version, statement, object_id, object_value,
                                       confidence, status, cardinality, polarity, asserted_by
                                FROM memories
                                WHERE subject_id = $1 AND predicate = $2 AND context_id = $3
                                  AND asserted_by != $4 AND superseded_at IS NULL AND status = 'active'
                                """,
                                subject_id,
                                predicate,
                                context_id,
                                asserted_by,
                            )
                            for other in other_active:
                                disagrees = False
                                if cardinality == "one" or other["cardinality"] == "one":
                                    if object_id is not None or other["object_id"] is not None:
                                        if object_id != other["object_id"]:
                                            disagrees = True
                                    elif obj_val is not None or other["object_value"] is not None:
                                        obj_val_json = json.dumps(obj_val) if obj_val is not None else None
                                        if obj_val_json != other["object_value"]:
                                            disagrees = True
                                    else:
                                        if statement.strip().lower() != other["statement"].strip().lower():
                                            disagrees = True

                                if polarity != other["polarity"]:
                                    same_object = False
                                    if object_id is not None and other["object_id"] is not None:
                                        same_object = (object_id == other["object_id"])
                                    elif obj_val is not None and other["object_value"] is not None:
                                        same_object = (json.dumps(obj_val) == other["object_value"])
                                    else:
                                        same_object = True
                                    if same_object:
                                        disagrees = True

                                if disagrees:
                                    # Add CONTRADICTS relation
                                    await conn.execute(
                                        """
                                        INSERT INTO memory_relations (id, type, from_memory, to_memory, confidence, created_event)
                                        VALUES ($1, 'CONTRADICTS', $2, $3, 1.0, $4)
                                        """,
                                        uuid.uuid4(),
                                        memory_id,
                                        other["id"],
                                        event_id,
                                    )
                                    # Add review_queue(kind='contradiction') row
                                    await conn.execute(
                                        """
                                        INSERT INTO review_queue (
                                            id, kind, memory_ids, entity_ids, reason, proposal, status, created_event, created_at
                                        )
                                        VALUES ($1, 'contradiction', $2, $3, $4, $5, 'open', $6, $7)
                                        """,
                                        uuid.uuid4(),
                                        [other["id"], memory_id],
                                        [e for e in [subject_id, object_id] if e is not None],
                                        f"Contradiction detected: speaker '{asserted_by}' asserted '{statement}' which conflicts with speaker '{other['asserted_by']}' assertion '{other['statement']}'.",
                                        json.dumps({
                                            "existing_memory": {
                                                "id": str(other["id"]),
                                                "asserted_by": other["asserted_by"],
                                                "statement": other["statement"],
                                            },
                                            "new_memory": {
                                                "id": str(memory_id),
                                                "asserted_by": asserted_by,
                                                "statement": statement,
                                            },
                                        }),
                                        event_id,
                                        now,
                                    )

                    # Step 3: Write claims row for third-party statements
                    if is_third_party:
                        await conn.execute(
                            """
                            INSERT INTO claims (id, source_id, event_id, claimant, text, credibility, memory_id, stance, created_at)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, 'supports', $8)
                            """,
                            uuid.uuid4(),
                            source_id,
                            event_id,
                            asserted_by,
                            statement,
                            confidence,
                            memory_id,
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
                        "valid_from": valid_from.isoformat() if valid_from else None,
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
