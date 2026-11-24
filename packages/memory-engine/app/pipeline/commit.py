"""
Stage 5: Canonical versioning, lineage, citations, and outbox commit.
Executes single-transaction commit for all resolved memory decisions.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
import re
import time
from typing import Any, Sequence
import uuid
import asyncpg
import dateparser

from app.domain.decide import Decision
from app.domain.salience import compute_salience
from app.pipeline.contracts import VerifiedMemory, DroppedMemory

logger = logging.getLogger("lumen.pipeline.commit")


def parse_valid_from(valid_from_text: str | None, occurred_at: datetime) -> datetime:
    """Parses relative anchor text into a valid_from datetime."""
    if not valid_from_text:
        return occurred_at

    clean_text = valid_from_text.strip()
    clean_text = re.sub(
        r"^(?:since|from|as of|in|starting from|starting)\s+",
        "",
        clean_text,
        flags=re.IGNORECASE,
    ).strip()

    try:
        parsed = dateparser.parse(
            clean_text,
            settings={
                "RELATIVE_BASE": occurred_at,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "past",
            },
        )
        if parsed:
            return parsed
    except Exception as e:
        logger.debug(f"Failed to parse valid_from_text '{valid_from_text}': {e}")

    return occurred_at


async def commit_pipeline_run(
    conn: asyncpg.Connection,
    event_id: uuid.UUID,
    context_id: uuid.UUID,
    source_id: uuid.UUID | None,
    event_speaker: str,
    occurred_at: datetime,
    resolved_decisions: Sequence[tuple[VerifiedMemory, Decision, uuid.UUID | None, uuid.UUID | None]],
    dropped_memories: Sequence[DroppedMemory],
    stage_ms: dict[str, int],
    model_name: str = "qwen2.5:7b-instruct",
) -> dict[str, Any]:
    """
    Executes atomic database transaction committing memories, citations, relations,
    outbox rows, review queue entries, and save_runs audit record.
    """
    t_commit_start = time.perf_counter()
    applied_memories: list[dict[str, Any]] = []
    review_ids: list[uuid.UUID] = []
    outbox_ids: list[int] = []

    counts = {
        "extracted": len(resolved_decisions) + len(dropped_memories),
        "verified": len(resolved_decisions),
        "dropped_unsupported": len(dropped_memories),
        "tier_a": 0,
        "tier_b": 0,
        "reaffirmed": 0,
        "superseded": 0,
        "created": 0,
    }

    for vm, decision, subject_id, object_id in resolved_decisions:
        mem = vm.extracted
        valid_from = mem.valid_from or parse_valid_from(mem.valid_from_text, occurred_at)
        salience_score = compute_salience(mem.importance, mem.type, mem.confidence)

        # Merge object_value and attributes if present
        stored_object_val = mem.object_value
        if mem.attributes:
            if isinstance(stored_object_val, dict):
                stored_object_val = {**stored_object_val, "attributes": mem.attributes}
            else:
                stored_object_val = {"value": stored_object_val, "attributes": mem.attributes}
        obj_val_json = json.dumps(stored_object_val) if stored_object_val is not None else None

        if decision.tier == "A":
            counts["tier_a"] += 1
        else:
            counts["tier_b"] += 1

        # -------------------------------------------------------------
        # 1. ACTION: REAFFIRM
        # -------------------------------------------------------------
        if decision.action == "reaffirm" and decision.target_memory_id and decision.target_lineage:
            counts["reaffirmed"] += 1
            cit_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO citations (id, memory_id, event_id, chunk_id, quote, char_start, char_end, verified, role)
                VALUES ($1, $2, $3, $4, $5, $6, $7, true, 'reaffirm')
                """,
                cit_id,
                decision.target_memory_id,
                event_id,
                vm.chunk_id,
                vm.quote,
                vm.char_start,
                vm.char_end,
            )

            await conn.execute(
                """
                INSERT INTO memory_signals (lineage_id, reaffirm_count, last_reaffirmed_at)
                VALUES ($1, 1, now())
                ON CONFLICT (lineage_id) DO UPDATE SET
                    reaffirm_count = memory_signals.reaffirm_count + 1,
                    last_reaffirmed_at = now()
                """,
                decision.target_lineage,
            )

            await conn.execute(
                """
                INSERT INTO events (id, kind, type, actor, source_id, context_id, content, content_hash, occurred_at, recorded_at, caused_by)
                VALUES ($1, 'DERIVED', 'memory.reaffirmed', 'system', $2, $3, $4, md5($4), now(), now(), $5)
                """,
                uuid.uuid4(),
                source_id,
                context_id,
                f"Reaffirmed memory {decision.target_memory_id}",
                [event_id],
            )
            continue

        # -------------------------------------------------------------
        # 2. ACTION: SUPERSEDE
        # -------------------------------------------------------------
        if decision.action == "supersede" and decision.target_memory_id and decision.target_lineage:
            counts["superseded"] += 1
            new_id = uuid.uuid4()

            old_version = (
                await conn.fetchval("SELECT version FROM memories WHERE id = $1", decision.target_memory_id)
            ) or 1
            new_version = old_version + 1

            # Insert new version first (to satisfy memories_superseded_by_fkey)
            await conn.execute(
                """
                INSERT INTO memories (
                    id, lineage_id, version, type, epistemic, statement,
                    subject_id, predicate, object_id, object_value, cardinality, polarity,
                    valid_from, valid_to, confidence, importance, status,
                    context_id, created_event, asserted_by, lane, sensitivity
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, null, $14, $15, 'active', $16, $17, $18, $19, $20)
                """,
                new_id,
                decision.target_lineage,
                new_version,
                mem.type,
                mem.epistemic,
                mem.statement,
                subject_id,
                mem.predicate,
                object_id,
                obj_val_json,
                mem.cardinality,
                mem.polarity,
                valid_from,
                mem.confidence,
                salience_score,
                context_id,
                event_id,
                mem.asserted_by,
                mem.lane,
                mem.sensitivity,
            )

            # Close old memory
            await conn.execute(
                """
                UPDATE memories
                SET status = 'superseded', superseded_at = now(), valid_to = $1, superseded_by = $2
                WHERE id = $3
                """,
                valid_from,
                new_id,
                decision.target_memory_id,
            )

            # Insert citation
            cit_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO citations (id, memory_id, event_id, chunk_id, quote, char_start, char_end, verified, role)
                VALUES ($1, $2, $3, $4, $5, $6, $7, true, 'evidence')
                """,
                cit_id,
                new_id,
                event_id,
                vm.chunk_id,
                vm.quote,
                vm.char_start,
                vm.char_end,
            )

            # SUPERSEDES relation
            await conn.execute(
                """
                INSERT INTO memory_relations (id, type, from_memory, to_memory, confidence, created_event)
                VALUES ($1, 'SUPERSEDES', $2, $3, $4, $5)
                """,
                uuid.uuid4(),
                new_id,
                decision.target_memory_id,
                mem.confidence,
                event_id,
            )

            # DERIVED event
            await conn.execute(
                """
                INSERT INTO events (id, kind, type, actor, source_id, context_id, content, content_hash, occurred_at, recorded_at, caused_by)
                VALUES ($1, 'DERIVED', 'memory.superseded', 'system', $2, $3, $4, md5($4), now(), now(), $5)
                """,
                uuid.uuid4(),
                source_id,
                context_id,
                f"Memory {decision.target_memory_id} superseded by {new_id}",
                [event_id],
            )

            # Outbox payload
            outbox_payload = {
                "memory_id": str(new_id),
                "lineage_id": str(decision.target_lineage),
                "version": new_version,
                "superseded_id": str(decision.target_memory_id),
                "secret": mem.sensitivity == "secret",
                "statement": "[REDACTED SECRET]" if mem.sensitivity == "secret" else mem.statement,
            }
            outbox_row = await conn.fetchrow(
                """
                INSERT INTO outbox (event_id, topic, payload, status)
                VALUES ($1, 'memory.superseded', $2, 'queued')
                RETURNING id
                """,
                event_id,
                json.dumps(outbox_payload),
            )
            if outbox_row:
                outbox_ids.append(outbox_row["id"])

            if mem.sensitivity != "secret":
                await conn.execute(
                    """
                    INSERT INTO jobs (kind, payload, status)
                    VALUES ('embed_memory', $1, 'queued')
                    """,
                    json.dumps({"memory_id": str(new_id)}),
                )

            applied_memories.append({
                "id": str(new_id),
                "lineage_id": str(decision.target_lineage),
                "version": new_version,
                "statement": mem.statement,
                "action": "superseded",
                "tier": decision.tier,
                "lane": mem.lane,
                "sensitivity": mem.sensitivity,
            })
            continue

        # -------------------------------------------------------------
        # 3. ACTION: CREATE or STAGE
        # -------------------------------------------------------------
        counts["created"] += 1
        new_id = uuid.uuid4()
        lineage_id = uuid.uuid4()
        status = "active" if decision.action == "create" else "pending_review"

        await conn.execute(
            """
            INSERT INTO memories (
                id, lineage_id, version, type, epistemic, statement,
                subject_id, predicate, object_id, object_value, cardinality, polarity,
                valid_from, valid_to, confidence, importance, status,
                context_id, created_event, asserted_by, lane, sensitivity
            )
            VALUES ($1, $2, 1, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, null, $13, $14, $15, $16, $17, $18, $19, $20)
            """,
            new_id,
            lineage_id,
            mem.type,
            mem.epistemic,
            mem.statement,
            subject_id,
            mem.predicate,
            object_id,
            obj_val_json,
            mem.cardinality,
            mem.polarity,
            valid_from,
            mem.confidence,
            salience_score,
            status,
            context_id,
            event_id,
            mem.asserted_by,
            mem.lane,
            mem.sensitivity,
        )

        # Insert Citation
        cit_id = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO citations (id, memory_id, event_id, chunk_id, quote, char_start, char_end, verified, role)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'evidence')
            """,
            cit_id,
            new_id,
            event_id,
            vm.chunk_id,
            vm.quote,
            vm.char_start,
            vm.char_end,
            vm.grounding_passed,
        )

        # Entity MENTIONS relations
        if subject_id:
            await conn.execute(
                """
                INSERT INTO memory_relations (id, type, from_memory, to_entity, confidence, created_event)
                VALUES ($1, 'MENTIONS', $2, $3, $4, $5)
                """,
                uuid.uuid4(),
                new_id,
                subject_id,
                mem.confidence,
                event_id,
            )
        if object_id:
            await conn.execute(
                """
                INSERT INTO memory_relations (id, type, from_memory, to_entity, confidence, created_event)
                VALUES ($1, 'MENTIONS', $2, $3, $4, $5)
                """,
                uuid.uuid4(),
                new_id,
                object_id,
                mem.confidence,
                event_id,
            )

        # Signals
        await conn.execute(
            "INSERT INTO memory_signals (lineage_id, reaffirm_count, last_reaffirmed_at) VALUES ($1, 0, now()) ON CONFLICT DO NOTHING",
            lineage_id,
        )

        # If third party assertion, log claim
        if mem.asserted_by != event_speaker and source_id:
            await conn.execute(
                """
                INSERT INTO claims (id, source_id, event_id, claimant, text, credibility, memory_id, stance)
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'supports')
                """,
                uuid.uuid4(),
                source_id,
                event_id,
                mem.asserted_by,
                mem.statement,
                mem.confidence,
                new_id,
            )

        # Review Queue if staged
        if decision.action == "stage":
            kind = "contradiction" if decision.contradicts else "low_confidence"
            if decision.reason == "value_not_in_quote":
                kind = "low_confidence"
            
            # CONTRADICTS relations
            for c_id in decision.contradicts:
                await conn.execute(
                    """
                    INSERT INTO memory_relations (id, type, from_memory, to_memory, confidence, created_event)
                    VALUES ($1, 'CONTRADICTS', $2, $3, $4, $5)
                    """,
                    uuid.uuid4(),
                    new_id,
                    c_id,
                    mem.confidence,
                    event_id,
                )

            proposal_data = {
                "memory_id": str(new_id),
                "statement": mem.statement,
                "reason": decision.reason,
                "contradicts": [str(c) for c in decision.contradicts],
            }
            rq_row = await conn.fetchrow(
                """
                INSERT INTO review_queue (kind, reason, proposal, memory_ids)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                kind,
                decision.reason,
                json.dumps(proposal_data),
                [new_id],
            )
            if rq_row:
                review_ids.append(rq_row["id"])

        # DERIVED event
        derived_type = "memory.created" if status == "active" else "memory.staged"
        await conn.execute(
            """
            INSERT INTO events (id, kind, type, actor, source_id, context_id, content, content_hash, occurred_at, recorded_at, caused_by)
            VALUES ($1, 'DERIVED', $2, 'system', $3, $4, $5, md5($5), now(), now(), $6)
            """,
            uuid.uuid4(),
            derived_type,
            source_id,
            context_id,
            f"Memory {new_id} {status}",
            [event_id],
        )

        # Outbox row
        outbox_payload = {
            "memory_id": str(new_id),
            "lineage_id": str(lineage_id),
            "version": 1,
            "status": status,
            "lane": mem.lane,
            "secret": mem.sensitivity == "secret",
            "statement": "[REDACTED SECRET]" if mem.sensitivity == "secret" else mem.statement,
        }
        outbox_row = await conn.fetchrow(
            """
            INSERT INTO outbox (event_id, topic, payload, status)
            VALUES ($1, 'memory.created', $2, 'queued')
            RETURNING id
            """,
            event_id,
            json.dumps(outbox_payload),
        )
        if outbox_row:
            outbox_ids.append(outbox_row["id"])

        if mem.sensitivity != "secret":
            await conn.execute(
                """
                INSERT INTO jobs (kind, payload, status)
                VALUES ('embed_memory', $1, 'queued')
                """,
                json.dumps({"memory_id": str(new_id)}),
            )

        applied_memories.append({
            "id": str(new_id),
            "lineage_id": str(lineage_id),
            "version": 1,
            "statement": mem.statement,
            "action": "created",
            "tier": decision.tier,
            "lane": mem.lane,
            "sensitivity": mem.sensitivity,
        })

    # 4. Insert save_runs audit record
    stage_ms["commit"] = max(1, int((time.perf_counter() - t_commit_start) * 1000))
    dropped_json = json.dumps([{"statement": d.statement, "reason": d.reason} for d in dropped_memories])
    await conn.execute(
        """
        INSERT INTO save_runs (event_id, stage_ms, counts, dropped, model)
        VALUES ($1, $2, $3, $4, $5)
        """,
        event_id,
        json.dumps(stage_ms),
        json.dumps(counts),
        dropped_json,
        model_name,
    )

    return {
        "event_id": event_id,
        "stage_ms": stage_ms,
        "counts": counts,
        "dropped": [{"statement": d.statement, "reason": d.reason} for d in dropped_memories],
        "memories": applied_memories,
        "review_ids": review_ids,
        "outbox_ids": outbox_ids,
    }
