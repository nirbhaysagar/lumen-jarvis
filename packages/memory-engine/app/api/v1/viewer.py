"""
Glass-Box Memory Viewer API (Phase 1 - V1).
Endpoints for exploring, auditing, diffing, correcting, and tombstoning memories.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
from typing import Any, List, Optional, Literal
import uuid
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.domain.salience import compute_salience

logger = logging.getLogger("lumen.viewer")
router = APIRouter(prefix="/v1/viewer", tags=["viewer"])


def get_db_pool(request: Request) -> asyncpg.Pool:
    storage = getattr(request.app.state, "storage", None)
    pool = getattr(request.app.state, "db_pool", None) or (storage.pool if storage else None)
    if not pool:
        raise HTTPException(status_code=500, detail="Database pool not available")
    return pool


class MemoryCorrectionRequest(BaseModel):
    statement: Optional[str] = Field(None, min_length=1)
    lane: Optional[Literal["personal", "work", "code", "chat"]] = None
    sensitivity: Optional[Literal["normal", "private", "secret"]] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class ReviewResolutionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    notes: Optional[str] = None


@router.get("/stats")
async def get_viewer_stats(pool: asyncpg.Pool = Depends(get_db_pool)) -> dict[str, Any]:
    """Summary dashboard statistics for memory engine."""
    async with pool.acquire() as conn:
        active_count = await conn.fetchval("SELECT count(*) FROM memories WHERE status = 'active'")
        lineage_count = await conn.fetchval("SELECT count(DISTINCT lineage_id) FROM memories")
        review_count = await conn.fetchval("SELECT count(*) FROM review_queue WHERE status IN ('open', 'pending')")
        outbox_pending = await conn.fetchval("SELECT count(*) FROM outbox WHERE status = 'queued'")

        lane_rows = await conn.fetch("SELECT lane, count(*) AS cnt FROM memories WHERE status = 'active' GROUP BY lane")
        lanes = {r["lane"]: r["cnt"] for r in lane_rows if r["lane"]}

        sens_rows = await conn.fetch("SELECT sensitivity, count(*) AS cnt FROM memories WHERE status = 'active' GROUP BY sensitivity")
        sensitivities = {r["sensitivity"]: r["cnt"] for r in sens_rows if r["sensitivity"]}

        return {
            "active_memories": active_count or 0,
            "lineages": lineage_count or 0,
            "pending_reviews": review_count or 0,
            "outbox_pending": outbox_pending or 0,
            "lanes": lanes,
            "sensitivities": sensitivities,
        }


@router.get("/memories")
async def list_viewer_memories(
    lane: Optional[str] = None,
    status: str = "active",
    sensitivity: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Lists memories with optional filtering, citations, and search."""
    async with pool.acquire() as conn:
        conditions = []
        params = []
        p_idx = 1

        if status != "all":
            actual_status = "retracted" if status == "tombstoned" else status
            conditions.append(f"m.status = ${p_idx}")
            params.append(actual_status)
            p_idx += 1

        if lane:
            conditions.append(f"m.lane = ${p_idx}")
            params.append(lane)
            p_idx += 1

        if sensitivity:
            conditions.append(f"m.sensitivity = ${p_idx}")
            params.append(sensitivity)
            p_idx += 1

        if search:
            conditions.append(f"m.statement ILIKE ${p_idx}")
            params.append(f"%{search.strip()}%")
            p_idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Total count query
        count_query = f"SELECT count(*) FROM memories m {where_clause}"
        total = await conn.fetchval(count_query, *params)

        # Select items
        items_query = f"""
            SELECT m.id, m.lineage_id, m.version, m.type, m.epistemic, m.statement,
                   m.confidence, m.importance, m.recorded_at, m.status, m.lane, m.sensitivity,
                   m.valid_from, m.valid_to, m.superseded_at, m.superseded_by,
                   sub.canonical_name AS subject_name,
                   obj.canonical_name AS object_name,
                   COALESCE(ms.reaffirm_count, 0) AS reaffirm_count,
                   COALESCE(ms.access_count, 0) AS access_count
            FROM memories m
            LEFT JOIN entities sub ON m.subject_id = sub.id
            LEFT JOIN entities obj ON m.object_id = obj.id
            LEFT JOIN memory_signals ms ON ms.lineage_id = m.lineage_id
            {where_clause}
            ORDER BY m.recorded_at DESC
            LIMIT ${p_idx} OFFSET ${p_idx + 1}
        """
        rows = await conn.fetch(items_query, *params, limit, offset)

        mem_ids = [r["id"] for r in rows]
        cit_map = {}
        if mem_ids:
            cit_rows = await conn.fetch(
                """
                SELECT c.memory_id, c.event_id, c.quote, c.char_start, c.char_end,
                       e.occurred_at AS said_at, s.title AS source_title, s.kind AS source_kind
                FROM citations c
                JOIN events e ON c.event_id = e.id
                LEFT JOIN sources s ON e.source_id = s.id
                WHERE c.memory_id = ANY($1::uuid[])
                """,
                mem_ids,
            )
            for c in cit_rows:
                cit_map.setdefault(c["memory_id"], []).append(dict(c))

        items = []
        for r in rows:
            mid = r["id"]
            cits = cit_map.get(mid, [])
            items.append({
                "id": str(mid),
                "lineage_id": str(r["lineage_id"]),
                "version": r["version"],
                "statement": r["statement"],
                "type": r["type"],
                "epistemic": r["epistemic"],
                "lane": r["lane"],
                "sensitivity": r["sensitivity"],
                "status": r["status"],
                "confidence": float(r["confidence"]),
                "importance": float(r["importance"]),
                "recorded_at": r["recorded_at"].isoformat() if r["recorded_at"] else None,
                "valid_from": r["valid_from"].isoformat() if r.get("valid_from") else None,
                "valid_to": r["valid_to"].isoformat() if r.get("valid_to") else None,
                "superseded_at": r["superseded_at"].isoformat() if r.get("superseded_at") else None,
                "superseded_by": str(r["superseded_by"]) if r.get("superseded_by") else None,
                "subject": r["subject_name"],
                "object": r["object_name"],
                "reaffirm_count": r["reaffirm_count"],
                "access_count": r["access_count"],
                "citations": [
                    {
                        "event_id": str(c["event_id"]),
                        "quote": c["quote"],
                        "char_start": c["char_start"],
                        "char_end": c["char_end"],
                        "said_at": c["said_at"].isoformat() if isinstance(c.get("said_at"), datetime) else str(c.get("said_at")),
                        "source_title": c["source_title"],
                        "source_kind": c["source_kind"],
                    }
                    for c in cits
                ],
            })

        return {
            "total": total or 0,
            "limit": limit,
            "offset": offset,
            "items": items,
        }


@router.get("/memories/{lineage_id}/history")
async def get_memory_lineage_history(
    lineage_id: uuid.UUID,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Returns all historical versions of a memory lineage with diffs and timestamps."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.id, m.lineage_id, m.version, m.type, m.epistemic, m.statement,
                   m.confidence, m.importance, m.recorded_at, m.status, m.lane, m.sensitivity,
                   m.valid_from, m.valid_to, m.superseded_at, m.superseded_by,
                   sub.canonical_name AS subject_name,
                   obj.canonical_name AS object_name
            FROM memories m
            LEFT JOIN entities sub ON m.subject_id = sub.id
            LEFT JOIN entities obj ON m.object_id = obj.id
            WHERE m.lineage_id = $1
            ORDER BY m.version ASC
            """,
            lineage_id,
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Lineage not found")

        mem_ids = [r["id"] for r in rows]
        cit_rows = await conn.fetch(
            """
            SELECT c.memory_id, c.event_id, c.quote, c.char_start, c.char_end,
                   e.occurred_at AS said_at, s.title AS source_title, s.kind AS source_kind
            FROM citations c
            JOIN events e ON c.event_id = e.id
            LEFT JOIN sources s ON e.source_id = s.id
            WHERE c.memory_id = ANY($1::uuid[])
            """,
            mem_ids,
        )
        cit_map = {}
        for c in cit_rows:
            cit_map.setdefault(c["memory_id"], []).append(dict(c))

        versions = []
        prev_statement = None
        for r in rows:
            mid = r["id"]
            cits = cit_map.get(mid, [])
            v_dict = {
                "id": str(mid),
                "version": r["version"],
                "statement": r["statement"],
                "type": r["type"],
                "epistemic": r["epistemic"],
                "lane": r["lane"],
                "sensitivity": r["sensitivity"],
                "status": r["status"],
                "confidence": float(r["confidence"]),
                "recorded_at": r["recorded_at"].isoformat() if r["recorded_at"] else None,
                "valid_from": r["valid_from"].isoformat() if r.get("valid_from") else None,
                "valid_to": r["valid_to"].isoformat() if r.get("valid_to") else None,
                "superseded_at": r["superseded_at"].isoformat() if r.get("superseded_at") else None,
                "superseded_by": str(r["superseded_by"]) if r.get("superseded_by") else None,
                "citations": [
                    {
                        "event_id": str(c["event_id"]),
                        "quote": c["quote"],
                        "source_title": c["source_title"],
                    }
                    for c in cits
                ],
                "previous_statement": prev_statement,
            }
            versions.append(v_dict)
            prev_statement = r["statement"]

        return {
            "lineage_id": str(lineage_id),
            "version_count": len(versions),
            "versions": versions,
        }


@router.patch("/memories/{memory_id}")
async def correct_memory(
    memory_id: uuid.UUID,
    req: MemoryCorrectionRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """
    User manual correction endpoint:
    Supersedes the existing active memory and creates a new version with the corrected values.
    Enforces relational integrity and bi-temporal validity.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            old = await conn.fetchrow(
                "SELECT * FROM memories WHERE id = $1 FOR UPDATE",
                memory_id,
            )
            if not old:
                raise HTTPException(status_code=404, detail="Memory not found")
            if old["status"] != "active":
                raise HTTPException(status_code=400, detail=f"Cannot correct non-active memory (status={old['status']})")

            new_id = uuid.uuid4()
            new_version = old["version"] + 1
            now = datetime.now(timezone.utc)

            new_statement = req.statement.strip() if req.statement else old["statement"]
            new_lane = req.lane or old["lane"]
            new_sensitivity = req.sensitivity or old["sensitivity"]
            new_conf = req.confidence if req.confidence is not None else old["confidence"]

            # Insert new version first
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
                    $16, $17, 'user', $18, $19, $20
                )
                """,
                new_id,
                old["lineage_id"],
                new_version,
                old["type"],
                old["epistemic"],
                new_statement,
                old["subject_id"],
                old["predicate"],
                old["object_id"],
                old["object_value"],
                old["cardinality"],
                old["polarity"],
                now,
                new_conf,
                old["importance"],
                old["context_id"],
                old["created_event"],
                new_lane,
                new_sensitivity,
                now,
            )

            # Close old memory
            await conn.execute(
                """
                UPDATE memories
                SET status = 'superseded', superseded_at = $1, valid_to = $1, superseded_by = $2
                WHERE id = $3
                """,
                now,
                new_id,
                memory_id,
            )

            # DERIVED event for correction
            ev_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO events (id, kind, type, actor, context_id, target_id, content, content_hash, occurred_at, recorded_at, caused_by)
                VALUES ($1, 'DERIVED', 'memory.corrected', 'user', $2, $3, $4, md5($4), $5, $5, $6)
                """,
                ev_id,
                old["context_id"],
                new_id,
                f"Memory {memory_id} manually corrected to {new_id}: {new_statement}",
                now,
                [old["created_event"]],
            )

            # Outbox row
            ob_payload = {
                "memory_id": str(new_id),
                "lineage_id": str(old["lineage_id"]),
                "version": new_version,
                "superseded_id": str(memory_id),
                "statement": "[REDACTED SECRET]" if new_sensitivity == "secret" else new_statement,
                "lane": new_lane,
                "sensitivity": new_sensitivity,
            }
            await conn.execute(
                """
                INSERT INTO outbox (event_id, topic, payload, status)
                VALUES ($1, 'memory.corrected', $2, 'queued')
                """,
                ev_id,
                json.dumps(ob_payload),
            )

            return {
                "id": str(new_id),
                "lineage_id": str(old["lineage_id"]),
                "version": new_version,
                "statement": new_statement,
                "lane": new_lane,
                "sensitivity": new_sensitivity,
                "status": "active",
                "superseded_id": str(memory_id),
            }


@router.delete("/memories/{memory_id}")
async def tombstone_memory(
    memory_id: uuid.UUID,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """
    User manual forget endpoint:
    Tombstones the memory in-place (updating status='tombstoned', valid_to=now, superseded_at=now)
    and records an immutable tombstone DERIVED event.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, context_id, statement, status, lineage_id, created_event FROM memories WHERE id = $1 FOR UPDATE",
                memory_id,
            )
            if not row:
                raise HTTPException(status_code=404, detail="Memory not found")
            if row["status"] == "retracted":
                return {"id": str(memory_id), "status": "already_retracted"}

            now = datetime.now(timezone.utc)
            # Update memory status to retracted (allowed by memories_closeout_only trigger)
            await conn.execute(
                """
                UPDATE memories
                SET status = 'retracted', valid_to = $1, superseded_at = $1
                WHERE id = $2
                """,
                now,
                memory_id,
            )

            # Append tombstone DERIVED event
            ev_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO events (id, kind, type, actor, context_id, target_id, content, content_hash, tombstone, occurred_at, recorded_at, caused_by)
                VALUES ($1, 'DERIVED', 'memory.tombstoned', 'user', $2, $3, $4, md5($4), true, $5, $5, $6)
                """,
                ev_id,
                row["context_id"],
                memory_id,
                f"Memory {memory_id} tombstoned by user request",
                now,
                [row["created_event"]],
            )

            # Outbox row
            await conn.execute(
                """
                INSERT INTO outbox (event_id, topic, payload, status)
                VALUES ($1, 'memory.tombstoned', $2, 'queued')
                """,
                ev_id,
                json.dumps({"memory_id": str(memory_id), "lineage_id": str(row["lineage_id"]), "status": "retracted"}),
            )

            return {
                "id": str(memory_id),
                "lineage_id": str(row["lineage_id"]),
                "status": "retracted",
            }


@router.get("/traces")
async def list_retrieval_traces(
    limit: int = Query(default=20, ge=1, le=100),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> list[dict[str, Any]]:
    """Lists recent retrieval traces showing channels, plan, latency, and returned memory IDs."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, query, as_of, plan, channels, fused, returned, latency_ms, created_at
            FROM retrieval_traces
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        results = []
        for r in rows:
            results.append({
                "id": str(r["id"]),
                "query": r["query"],
                "as_of": r["as_of"].isoformat() if r.get("as_of") else None,
                "latency_ms": r["latency_ms"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "plan": json.loads(r["plan"]) if isinstance(r["plan"], str) else r["plan"],
                "channels": json.loads(r["channels"]) if isinstance(r["channels"], str) else r["channels"],
                "fused": json.loads(r["fused"]) if isinstance(r["fused"], str) else r["fused"],
                "returned_ids": [str(x) for x in (r["returned"] or [])],
            })
        return results


@router.get("/review-queue")
async def list_review_queue(
    status: Optional[str] = "open",
    limit: int = Query(default=50, ge=1, le=100),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> list[dict[str, Any]]:
    """Lists staged proposals in review_queue waiting for human adjudication."""
    async with pool.acquire() as conn:
        conditions = []
        params = []
        if status and status != "all":
            if status in ("open", "pending"):
                conditions.append("rq.status IN ('open', 'pending')")
            else:
                params.append(status)
                conditions.append(f"rq.status = ${len(params)}")
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT rq.id, rq.kind, rq.reason, rq.proposal, rq.status, rq.created_at, rq.memory_ids
            FROM review_queue rq
            {where_clause}
            ORDER BY rq.created_at DESC
            LIMIT ${len(params) + 1}
        """
        params.append(limit)
        rows = await conn.fetch(query, *params)
        results = []
        for r in rows:
            p_data = json.loads(r["proposal"]) if isinstance(r["proposal"], str) else r["proposal"]
            mids = [str(x) for x in (r.get("memory_ids") or [])]
            results.append({
                "id": str(r["id"]),
                "kind": r["kind"],
                "reason": r["reason"],
                "status": r["status"],
                "memory_ids": mids,
                "memory_id": mids[0] if mids else None,
                "proposal": p_data,
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            })
        return results


@router.post("/review-queue/{proposal_id}/resolve")
async def resolve_review_proposal(
    proposal_id: uuid.UUID,
    req: ReviewResolutionRequest,
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> dict[str, Any]:
    """Approves or rejects a staged proposal in the review queue."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, memory_ids, status, proposal FROM review_queue WHERE id = $1 FOR UPDATE",
                proposal_id,
            )
            if not row:
                raise HTTPException(status_code=404, detail="Review queue item not found")

            new_status = "accepted" if req.decision == "approve" else "rejected"
            await conn.execute(
                "UPDATE review_queue SET status = $1, reviewed_at = now() WHERE id = $2",
                new_status,
                proposal_id,
            )

            mids = row.get("memory_ids") or []
            if mids:
                if req.decision == "approve":
                    await conn.execute("UPDATE memories SET status = 'active' WHERE id = ANY($1::uuid[])", mids)
                else:
                    await conn.execute(
                        "UPDATE memories SET status = 'retracted', valid_to = now(), superseded_at = now() WHERE id = ANY($1::uuid[])",
                        mids,
                    )

            return {
                "id": str(proposal_id),
                "decision": req.decision,
                "status": new_status,
                "memory_ids": [str(m) for m in mids],
            }
