"""
Lineage Service (Milestone F1 & F2).
Handles lineage-scoped memory operations:
- Retraction (forgetting) with audit trail, tombstone event, and outbox notification.
- Purging (redacting sensitive spans from raw text and quotes).
- Restoring (undo retract) by writing version v(n+1).
- Correction ("Actually it's X") with bi-temporal version increment, concurrency check,
  SUPERSEDES relation, citation pointing to correction event, and outbox event.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
from typing import Any, List, Optional, Tuple
import uuid
import asyncpg

from app.domain.errors import NotFoundError, ConflictError
from app.vault.projector import VaultProjector

logger = logging.getLogger("lumen.services.lineage")


def apply_redactions(text: str, redaction_spans: List[Tuple[int, int]]) -> str:
    """Replaces character spans [char_start, char_end) with [redacted]."""
    if not text or not redaction_spans:
        return text
    # Sort descending by start to preserve earlier indices
    sorted_spans = sorted(redaction_spans, key=lambda s: s[0], reverse=True)
    res = text
    for start, end in sorted_spans:
        s = max(0, start)
        e = min(len(res), end)
        if s <= e:
            res = res[:s] + "[redacted]" + res[e:]
    return res


class LineageService:
    def __init__(self, pool: asyncpg.Pool, vault_dir: Optional[Any] = None):
        self.pool = pool
        self.vault_dir = vault_dir

    async def retract(
        self,
        lineage_id: uuid.UUID,
        reason: str = "user requested forget",
        actor: str = "user",
        purge: bool = False,
        purge_spans: Optional[List[Tuple[int, int]]] = None,
    ) -> dict[str, Any]:
        """
        Retracts the current active version of a memory lineage.
        Enforces bi-temporal closeout, appends a tombstone event to events,
        and optionally records a privacy redaction.
        """
        now = datetime.now(timezone.utc)
        retract_ev_id = uuid.uuid4()

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Fetch current active memory with lock
                row = await conn.fetchrow(
                    """
                    SELECT * FROM memories
                    WHERE lineage_id = $1 AND status = 'active'
                    FOR UPDATE
                    """,
                    lineage_id,
                )
                if not row:
                    raise NotFoundError(f"Active memory with lineage_id {lineage_id} not found")

                mem_id = row["id"]
                db_version = row["version"]

                # 2. Close out current memory row
                await conn.execute(
                    """
                    UPDATE memories
                    SET status = 'retracted', valid_to = $1, superseded_at = $1
                    WHERE id = $2
                    """,
                    now,
                    mem_id,
                )

                # 3. Append tombstone event to events
                tombstone_content = f"Memory lineage {lineage_id} (v{db_version}) retracted: {reason}"
                await conn.execute(
                    """
                    INSERT INTO events (
                        id, kind, type, actor, context_id, target_id,
                        content, content_hash, tombstone, occurred_at, recorded_at, caused_by
                    )
                    VALUES ($1, 'DERIVED', 'memory.tombstoned', $2, $3, $4, $5, md5($5), true, $6, $6, $7)
                    """,
                    retract_ev_id,
                    actor,
                    row["context_id"],
                    mem_id,
                    tombstone_content,
                    now,
                    [row["created_event"]] if row.get("created_event") else [],
                )

                # 4. Enqueue outbox notification
                outbox_payload = {
                    "lineage_id": str(lineage_id),
                    "memory_id": str(mem_id),
                    "version": db_version,
                    "reason": reason,
                    "actor": actor,
                    "action": "retracted",
                }
                await conn.execute(
                    """
                    INSERT INTO outbox (event_id, topic, payload, status)
                    VALUES ($1, 'memory.retracted', $2, 'queued')
                    """,
                    retract_ev_id,
                    json.dumps(outbox_payload),
                )

                # 5. Optional redaction (for sensitive secrets like phone numbers / credentials)
                if purge:
                    spans = purge_spans or [(0, len(row["statement"]))]
                    for s_start, s_end in spans:
                        redact_id = uuid.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO redactions (id, event_id, char_start, char_end, reason, created_event)
                            VALUES ($1, $2, $3, $4, $5, $6)
                            """,
                            redact_id,
                            row["created_event"] or retract_ev_id,
                            s_start,
                            s_end,
                            f"Privacy purge: {reason}",
                            retract_ev_id,
                        )

        # 6. Rebuild vault projection (moves note to _retracted/ folder)
        try:
            projector = VaultProjector(self.pool, vault_dir=self.vault_dir)
            await projector.rebuild()
        except Exception as e:
            logger.warning(f"Vault projector rebuild after retract failed: {e}")

        logger.info(f"Retracted memory lineage {lineage_id} v{db_version} ({reason})")
        return {
            "action": "retracted",
            "lineage_id": str(lineage_id),
            "version": db_version,
            "statement": row["statement"],
            "purged": purge,
        }

    async def undo_retract(
        self,
        lineage_id: uuid.UUID,
        reason: str = "undo retract",
        actor: str = "user",
    ) -> dict[str, Any]:
        """
        Restores a retracted memory by creating version v(n+1) with status='active'.
        Guarantees database append-only immutability by never un-retracting v(n) in place.
        """
        now = datetime.now(timezone.utc)
        restore_ev_id = uuid.uuid4()
        new_mem_id = uuid.uuid4()

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Fetch latest retracted memory
                row = await conn.fetchrow(
                    """
                    SELECT * FROM memories
                    WHERE lineage_id = $1
                    ORDER BY version DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    lineage_id,
                )
                if not row:
                    raise NotFoundError(f"Lineage {lineage_id} not found")
                if row["status"] != "retracted":
                    raise ConflictError(f"Lineage {lineage_id} is not retracted (status: {row['status']})")

                old_id = row["id"]
                new_version = row["version"] + 1

                # Capture event for the undo instruction
                await conn.execute(
                    """
                    INSERT INTO events (
                        id, kind, type, actor, context_id, target_id,
                        content, content_hash, occurred_at, recorded_at, caused_by
                    )
                    VALUES ($1, 'CAPTURE', 'memory.restored', $2, $3, $4, $5, md5($5), $6, $6, $7)
                    """,
                    restore_ev_id,
                    actor,
                    row["context_id"],
                    new_mem_id,
                    f"Restored memory lineage {lineage_id}: {reason}",
                    now,
                    [old_id],
                )

                # Insert new version v(n+1) as active
                obj_val = json.dumps(row["object_value"]) if row.get("object_value") is not None else None
                await conn.execute(
                    """
                    INSERT INTO memories (
                        id, lineage_id, version, type, epistemic, statement,
                        subject_id, predicate, object_id, object_value, cardinality, polarity,
                        valid_from, valid_to, confidence, importance, status,
                        context_id, created_event, asserted_by, lane, sensitivity, recorded_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10, $11, $12,
                        $13, null, $14, $15, 'active',
                        $16, $17, $18, $19, $20, $21
                    )
                    """,
                    new_mem_id,
                    lineage_id,
                    new_version,
                    row["type"],
                    row["epistemic"],
                    row["statement"],
                    row["subject_id"],
                    row["predicate"],
                    row["object_id"],
                    obj_val,
                    row["cardinality"],
                    row["polarity"],
                    now,
                    float(row["confidence"] or 1.0),
                    float(row["importance"] or 0.5),
                    row["context_id"],
                    restore_ev_id,
                    actor,
                    row["lane"],
                    row["sensitivity"],
                    now,
                )

                # Link restoration relation
                await conn.execute(
                    """
                    INSERT INTO memory_relations (from_memory, type, to_memory, confidence, created_event)
                    VALUES ($1, 'SUPERSEDES', $2, 1.0, $3)
                    """,
                    new_mem_id,
                    old_id,
                    restore_ev_id,
                )

                # Outbox notification
                outbox_payload = {
                    "lineage_id": str(lineage_id),
                    "memory_id": str(new_mem_id),
                    "version": new_version,
                    "action": "restored",
                }
                await conn.execute(
                    """
                    INSERT INTO outbox (event_id, topic, payload, status)
                    VALUES ($1, 'memory.restored', $2, 'queued')
                    """,
                    restore_ev_id,
                    json.dumps(outbox_payload),
                )

                # Queue embedding job
                if row["sensitivity"] != "secret":
                    await conn.execute(
                        """
                        INSERT INTO jobs (kind, payload, status)
                        VALUES ('embed_memory', $1, 'queued')
                        """,
                        json.dumps({"memory_id": str(new_mem_id)}),
                    )

        # Rebuild vault projection (moves note back to memories/)
        try:
            projector = VaultProjector(self.pool, vault_dir=self.vault_dir)
            await projector.rebuild()
        except Exception as e:
            logger.warning(f"Vault projector rebuild after undo failed: {e}")

        logger.info(f"Restored memory lineage {lineage_id} to v{new_version}")
        return {
            "action": "restored",
            "lineage_id": str(lineage_id),
            "version": new_version,
            "statement": row["statement"],
        }

    async def correct(
        self,
        lineage_id: uuid.UUID,
        new_statement: str,
        reason: str = "user correction",
        actor: str = "user",
        base_version: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Applies a conversational correction ("Actually it's X") through lineage:
        1. Validates base_version concurrency under row lock (preventing race conditions / lost updates).
        2. Closes out version v(n).
        3. Inserts version v(n+1) with updated statement and evidence citation.
        4. Links SUPERSEDES relation and appends audit events.
        5. Updates vault projection and schedules embedding.
        """
        clean_new = new_statement.strip()
        if not clean_new:
            raise ValueError("Corrected statement cannot be empty")

        now = datetime.now(timezone.utc)
        capture_ev_id = uuid.uuid4()
        new_mem_id = uuid.uuid4()

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # 1. Fetch current active version with row lock
                row = await conn.fetchrow(
                    """
                    SELECT * FROM memories
                    WHERE lineage_id = $1 AND status = 'active'
                    FOR UPDATE
                    """,
                    lineage_id,
                )
                if not row:
                    raise NotFoundError(f"Active memory with lineage_id {lineage_id} not found")

                db_version = row["version"]
                if base_version is not None and base_version != db_version:
                    raise ConflictError(
                        f"Version conflict: current memory version is {db_version}, but base_version was {base_version}"
                    )

                new_version = db_version + 1
                old_id = row["id"]

                # 2. Append CAPTURE event for the correction utterance
                ev_payload = json.dumps({"reason": reason, "lineage_id": str(lineage_id), "base_version": db_version})
                await conn.execute(
                    """
                    INSERT INTO events (
                        id, kind, type, actor, context_id, content, payload,
                        content_hash, occurred_at, recorded_at
                    )
                    VALUES ($1, 'CAPTURE', 'user.correction', $2, $3, $4, $5, md5($4), $6, $6)
                    """,
                    capture_ev_id,
                    actor,
                    row["context_id"],
                    clean_new,
                    ev_payload,
                    now,
                )

                # 3. Insert version v(n+1) first so foreign key constraints are satisfied
                await conn.execute(
                    """
                    INSERT INTO memories (
                        id, lineage_id, version, type, epistemic, statement,
                        subject_id, predicate, object_id, object_value, cardinality, polarity,
                        valid_from, valid_to, confidence, importance, status,
                        context_id, created_event, asserted_by, lane, sensitivity, recorded_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6,
                        $7, $8, $9, $10, $11, $12,
                        $13, null, $14, $15, 'active',
                        $16, $17, $18, $19, $20, $21
                    )
                    """,
                    new_mem_id,
                    lineage_id,
                    new_version,
                    row["type"],
                    row["epistemic"],
                    clean_new,
                    row["subject_id"],
                    row["predicate"],
                    row["object_id"],
                    json.dumps(clean_new),  # object_value updated as jsonb
                    row["cardinality"],
                    row["polarity"],
                    now,
                    float(row["confidence"] or 1.0),
                    float(row["importance"] or 0.5),
                    row["context_id"],
                    capture_ev_id,
                    actor,
                    row["lane"],
                    row["sensitivity"],
                    now,
                )

                # 4. Close out version v(n) referencing new_mem_id
                await conn.execute(
                    """
                    UPDATE memories
                    SET status = 'superseded', valid_to = $1, superseded_at = $1, superseded_by = $2
                    WHERE id = $3
                    """,
                    now,
                    new_mem_id,
                    old_id,
                )

                # 5. Insert SUPERSEDES relation
                await conn.execute(
                    """
                    INSERT INTO memory_relations (from_memory, type, to_memory, confidence, created_event)
                    VALUES ($1, 'SUPERSEDES', $2, 1.0, $3)
                    """,
                    new_mem_id,
                    old_id,
                    capture_ev_id,
                )

                # 6. Insert evidence citation pointing to correction utterance
                await conn.execute(
                    """
                    INSERT INTO citations (memory_id, event_id, role, quote, char_start, char_end, verified)
                    VALUES ($1, $2, 'evidence', $3, 0, $4, true)
                    """,
                    new_mem_id,
                    capture_ev_id,
                    clean_new,
                    max(1, len(clean_new)),
                )

                # 7. Append DERIVED superseded event
                derived_ev_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO events (
                        id, kind, type, actor, context_id, target_id, content,
                        content_hash, occurred_at, recorded_at, caused_by
                    )
                    VALUES ($1, 'DERIVED', 'memory.superseded', $2, $3, $4, $5, md5($5), $6, $6, $7)
                    """,
                    derived_ev_id,
                    actor,
                    row["context_id"],
                    new_mem_id,
                    f"Lineage {lineage_id} corrected: v{db_version} -> v{new_version}",
                    now,
                    [capture_ev_id],
                )

                # 8. Outbox row
                outbox_payload = {
                    "lineage_id": str(lineage_id),
                    "memory_id": str(new_mem_id),
                    "superseded_id": str(old_id),
                    "old_version": db_version,
                    "new_version": new_version,
                    "old_statement": row["statement"],
                    "new_statement": clean_new,
                    "actor": actor,
                    "action": "corrected",
                }
                await conn.execute(
                    """
                    INSERT INTO outbox (event_id, topic, payload, status)
                    VALUES ($1, 'memory.corrected', $2, 'queued')
                    """,
                    derived_ev_id,
                    json.dumps(outbox_payload),
                )

                # 9. Schedule embed job
                if row["sensitivity"] != "secret":
                    await conn.execute(
                        """
                        INSERT INTO jobs (kind, payload, status)
                        VALUES ('embed_memory', $1, 'queued')
                        """,
                        json.dumps({"memory_id": str(new_mem_id)}),
                    )

        # 10. Re-project vault
        try:
            projector = VaultProjector(self.pool, vault_dir=self.vault_dir)
            await projector.rebuild()
        except Exception as e:
            logger.warning(f"Vault projector rebuild after correction failed: {e}")

        logger.info(f"Corrected memory lineage {lineage_id}: v{db_version} -> v{new_version}")
        return {
            "action": "corrected",
            "lineage_id": str(lineage_id),
            "previous_version": db_version,
            "new_version": new_version,
            "old_statement": row["statement"],
            "new_statement": clean_new,
        }

    async def get_history(self, lineage_id: uuid.UUID) -> List[dict[str, Any]]:
        """Returns complete historical audit trail for a lineage, ordered by version ASC."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT m.*, e.content AS created_event_content, e.occurred_at AS event_occurred_at
                FROM memories m
                LEFT JOIN events e ON m.created_event = e.id
                WHERE m.lineage_id = $1
                ORDER BY m.version ASC
                """,
                lineage_id,
            )
            return [dict(r) for r in rows]
