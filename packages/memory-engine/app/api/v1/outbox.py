"""
API route handlers for Transactional Outbox (v1).
Provides stats, polling, acknowledgement, and failure reporting for vault sync workers.
"""
from __future__ import annotations
from typing import Any, List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field


router = APIRouter(tags=["outbox"])


class OutboxFailRequest(BaseModel):
    error: str = Field(..., min_length=1)


class OutboxStatsResponse(BaseModel):
    queued: int
    processed: int
    dead: int
    total: int


def get_db_pool(request: Request):
    storage = getattr(request.app.state, "storage", None)
    pool = getattr(request.app.state, "db_pool", None) or (storage.pool if storage else None)
    if not pool:
        raise RuntimeError("Database pool not available in application state")
    return pool


@router.get("/v1/outbox/stats", response_model=OutboxStatsResponse)
@router.get("/api/v1/outbox/stats", response_model=OutboxStatsResponse)
async def get_outbox_stats(pool=Depends(get_db_pool)) -> OutboxStatsResponse:
    """Returns counts of outbox records grouped by status."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT status, COUNT(*)::int AS count
            FROM outbox
            GROUP BY status
            """
        )
        counts = {r["status"]: r["count"] for r in rows}
        queued = counts.get("queued", 0)
        processed = counts.get("processed", 0)
        dead = counts.get("dead", 0)
        total = sum(counts.values())
        return OutboxStatsResponse(
            queued=queued,
            processed=processed,
            dead=dead,
            total=total,
        )


@router.get("/v1/outbox/pending")
@router.get("/api/v1/outbox/pending")
async def list_pending_outbox(
    limit: int = Query(default=50, ge=1, le=500),
    pool=Depends(get_db_pool),
) -> dict[str, Any]:
    """Fetch pending outbox records waiting to be drained into Obsidian vault."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, event_id, topic, payload, status, attempts, created_at
            FROM outbox
            WHERE status = 'queued'
            ORDER BY id ASC
            LIMIT $1
            """,
            limit,
        )
        items = []
        for r in rows:
            items.append({
                "id": str(r["id"]),
                "event_id": str(r["event_id"]) if r["event_id"] else None,
                "topic": r["topic"],
                "aggregate_type": r["topic"],
                "payload": r["payload"],
                "status": r["status"],
                "attempts": r["attempts"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            })
        return {"items": items, "count": len(items)}


@router.post("/v1/outbox/{outbox_id}/ack")
@router.post("/api/v1/outbox/{outbox_id}/ack")
async def ack_outbox_record(
    outbox_id: int,
    pool=Depends(get_db_pool),
) -> dict[str, Any]:
    """Acknowledge successful sync of an outbox record."""
    async with pool.acquire() as conn:
        res = await conn.execute(
            """
            UPDATE outbox
            SET status = 'processed', processed_at = now()
            WHERE id = $1
            """,
            outbox_id,
        )
        if res == "UPDATE 0":
            raise HTTPException(status_code=404, detail=f"Outbox record {outbox_id} not found")
        return {"ok": True, "id": outbox_id, "status": "processed"}


@router.post("/v1/outbox/{outbox_id}/fail")
@router.post("/api/v1/outbox/{outbox_id}/fail")
async def fail_outbox_record(
    outbox_id: int,
    req: OutboxFailRequest,
    pool=Depends(get_db_pool),
) -> dict[str, Any]:
    """Mark an outbox record as failed with an error detail string."""
    async with pool.acquire() as conn:
        res = await conn.execute(
            """
            UPDATE outbox
            SET status = 'dead', last_error = $2, attempts = attempts + 1
            WHERE id = $1
            """,
            outbox_id,
            req.error,
        )
        if res == "UPDATE 0":
            raise HTTPException(status_code=404, detail=f"Outbox record {outbox_id} not found")
        return {"ok": True, "id": outbox_id, "status": "dead"}
