"""
Stage 4: Entity and contradiction resolution.
Resolves entities to canonical IDs, looks up existing memories,
and executes pure decide() for Tier A auto-commit vs Tier B human review.
"""
from __future__ import annotations
import json
import logging
from typing import Sequence
import uuid
import asyncpg

from app.domain.decide import CandidateMemory, ExistingMemory, Decision, decide
from app.domain.match import has_change_cue, pick_predecessor, statements_conflict
from app.pipeline.contracts import ExtractedEntity, VerifiedMemory

logger = logging.getLogger("lumen.pipeline.resolve")


class EntityResolver:
    def __init__(self, conn: asyncpg.Connection, event_id: uuid.UUID, context_id: uuid.UUID):
        self.conn = conn
        self.event_id = event_id
        self.context_id = context_id
        self.ref_to_id: dict[str, uuid.UUID] = {}

    async def resolve_entities(self, entities: Sequence[ExtractedEntity]) -> dict[str, uuid.UUID]:
        """Resolves extracted entities to canonical UUIDs."""
        for ent in entities:
            clean_name = ent.name.strip()
            kind = ent.kind

            # 1. Exact match by canonical_name and kind
            row = await self.conn.fetchrow(
                "SELECT id FROM entities WHERE lower(canonical_name) = lower($1) AND kind = $2",
                clean_name,
                kind,
            )
            if row:
                self.ref_to_id[ent.ref] = row["id"]
                continue

            # 2. Check aliases
            alias_row = await self.conn.fetchrow(
                """
                SELECT ea.entity_id 
                FROM entity_aliases ea
                JOIN entities e ON e.id = ea.entity_id
                WHERE lower(ea.alias) = lower($1) AND e.kind = $2
                """,
                clean_name,
                kind,
            )
            if alias_row:
                self.ref_to_id[ent.ref] = alias_row["entity_id"]
                continue

            # 3. Create new entity
            new_id = uuid.uuid4()
            await self.conn.execute(
                """
                INSERT INTO entities (id, canonical_name, kind, context_id, created_event)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (canonical_name, kind) DO UPDATE SET canonical_name = EXCLUDED.canonical_name
                RETURNING id
                """,
                new_id,
                clean_name,
                kind,
                self.context_id,
                self.event_id,
            )
            row = await self.conn.fetchrow(
                "SELECT id FROM entities WHERE lower(canonical_name) = lower($1) AND kind = $2",
                clean_name,
                kind,
            )
            ent_id = row["id"] if row else new_id
            self.ref_to_id[ent.ref] = ent_id

            # Insert aliases
            for alias in ent.aliases:
                c_alias = alias.strip()
                if c_alias and c_alias.lower() != clean_name.lower():
                    await self.conn.execute(
                        "INSERT INTO entity_aliases (entity_id, alias) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        ent_id,
                        c_alias,
                    )

            # Outbox notification for entity creation
            outbox_payload = {
                "entity_id": str(ent_id),
                "canonical_name": clean_name,
                "kind": kind,
                "aliases": ent.aliases,
            }
            await self.conn.execute(
                """
                INSERT INTO outbox (event_id, topic, payload, status)
                VALUES ($1, 'entity.upserted', $2, 'queued')
                """,
                self.event_id,
                json.dumps(outbox_payload),
            )

        return self.ref_to_id


def _existing_from_row(row, subject_id, predicate) -> ExistingMemory:
    return ExistingMemory(
        id=row["id"],
        lineage_id=row["lineage_id"],
        version=row["version"],
        statement=row["statement"],
        subject_id=subject_id,
        predicate=predicate or row["predicate"],
        object_id=row["object_id"],
        object_value=row["object_value"],
        cardinality=row["cardinality"],
        polarity=row["polarity"],
        asserted_by=row["asserted_by"],
        status=row["status"],
    )


async def _active_memories(conn: asyncpg.Connection, context_id: uuid.UUID, speaker: str):
    return await conn.fetch(
        """
        SELECT id, lineage_id, version, statement, subject_id, predicate, object_id, object_value,
               cardinality, polarity, asserted_by, status
        FROM memories
        WHERE context_id = $1 AND lower(asserted_by) = lower($2)
          AND superseded_at IS NULL AND status = 'active'
        ORDER BY recorded_at DESC
        LIMIT 30
        """,
        context_id,
        speaker,
    )


async def _active_in_context(conn: asyncpg.Connection, context_id: uuid.UUID):
    """Every open memory in this workspace, whatever the extractor put in asserted_by."""
    return await conn.fetch(
        """
        SELECT id, lineage_id, version, statement, subject_id, predicate, object_id, object_value,
               cardinality, polarity, asserted_by, status
        FROM memories
        WHERE context_id = $1
          AND superseded_at IS NULL AND status = 'active'
        ORDER BY recorded_at DESC
        LIMIT 30
        """,
        context_id,
    )


async def resolve_decisions(
    conn: asyncpg.Connection,
    context_id: uuid.UUID,
    verified_memories: Sequence[VerifiedMemory],
    ref_to_id: dict[str, uuid.UUID],
) -> list[tuple[VerifiedMemory, Decision, uuid.UUID | None, uuid.UUID | None]]:
    """
    Looks up existing memories in the database and computes pure decide()
    for each verified memory.
    """
    resolved: list[tuple[VerifiedMemory, Decision, uuid.UUID | None, uuid.UUID | None]] = []

    for vm in verified_memories:
        mem = vm.extracted
        subject_id = ref_to_id.get(mem.subject_ref) if mem.subject_ref else None
        object_id = ref_to_id.get(mem.object_ref) if mem.object_ref else None

        if not mem.change_cue and has_change_cue(mem.statement):
            mem.change_cue = True

        # Build CandidateMemory
        cand = CandidateMemory(
            statement=mem.statement,
            subject_id=subject_id,
            predicate=mem.predicate,
            object_id=object_id,
            object_value=mem.object_value,
            cardinality=mem.cardinality,
            polarity=mem.polarity,
            change_cue=mem.change_cue,
            confidence=mem.confidence,
            epistemic=mem.epistemic,
            asserted_by=mem.asserted_by,
            lane=mem.lane,
            sensitivity=mem.sensitivity,
            grounding_passed=vm.grounding_passed,
        )

        own_existing: ExistingMemory | None = None
        others_existing: list[ExistingMemory] = []

        if subject_id and mem.predicate:
            # Query own-speaker active row
            own_row = await conn.fetchrow(
                """
                SELECT id, lineage_id, version, statement, object_id, object_value,
                       cardinality, polarity, asserted_by, status
                FROM memories
                WHERE subject_id = $1 AND predicate = $2 AND context_id = $3
                  AND asserted_by = $4 AND superseded_at IS NULL AND status = 'active'
                ORDER BY version DESC LIMIT 1
                """,
                subject_id,
                mem.predicate,
                context_id,
                mem.asserted_by,
            )
            if own_row:
                own_existing = ExistingMemory(
                    id=own_row["id"],
                    lineage_id=own_row["lineage_id"],
                    version=own_row["version"],
                    statement=own_row["statement"],
                    subject_id=subject_id,
                    predicate=mem.predicate,
                    object_id=own_row["object_id"],
                    object_value=own_row["object_value"],
                    cardinality=own_row["cardinality"],
                    polarity=own_row["polarity"],
                    asserted_by=own_row["asserted_by"],
                    status=own_row["status"],
                )

            # Query other speakers' active rows
            other_rows = await conn.fetch(
                """
                SELECT id, lineage_id, version, statement, object_id, object_value,
                       cardinality, polarity, asserted_by, status
                FROM memories
                WHERE subject_id = $1 AND predicate = $2 AND context_id = $3
                  AND asserted_by != $4 AND superseded_at IS NULL AND status = 'active'
                ORDER BY version DESC
                """,
                subject_id,
                mem.predicate,
                context_id,
                mem.asserted_by,
            )
            for r in other_rows:
                others_existing.append(_existing_from_row(r, subject_id, mem.predicate))

        # Predicate strings drift ("uses_database" vs "primary_database").
        # A change still closes the memory it names, and another speaker's
        # disagreement is staged even when the predicate does not match.
        if own_existing is None and cand.change_cue:
            # Extractors label the same fact "user", "lumen", or "we".
            # A change closes whichever open memory it actually names.
            own_rows = list(await _active_in_context(conn, context_id))
            chosen = pick_predecessor(mem.statement, [r["statement"] for r in own_rows])
            if chosen is not None:
                own_existing = _existing_from_row(
                    own_rows[chosen],
                    own_rows[chosen]["subject_id"],
                    own_rows[chosen]["predicate"],
                )

        if cand.asserted_by.lower() != "user":
            user_rows = await _active_memories(conn, context_id, "user")
            known_ids = {row.id for row in others_existing}
            for row in user_rows:
                if row["id"] in known_ids:
                    continue
                if statements_conflict(row["statement"], mem.statement):
                    others_existing.append(_existing_from_row(row, row["subject_id"], row["predicate"]))

        decision = decide(cand, own_existing, others_existing)
        resolved.append((vm, decision, subject_id, object_id))

    return resolved
