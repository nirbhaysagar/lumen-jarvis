"""
Event Replay Engine (Milestone X1).
Replays raw CAPTURE events through the SavePipeline to rebuild derived L1/L2 memory state
from scratch (used when upgrading extraction models or schemas).
"""
from __future__ import annotations
import logging
from typing import Any
import asyncpg

from app.pipeline import SavePipeline

logger = logging.getLogger("lumen.portability.replay")


async def replay_from_events(
    pool: asyncpg.Pool,
    *,
    dry_run: bool = False,
    limit: int = 1000,
) -> dict[str, Any]:
    """
    Fetches all CAPTURE events in chronological order and replays them through SavePipeline.
    Returns summary statistics and any errors encountered.
    """
    async with pool.acquire() as conn:
        old_mem_count = await conn.fetchval("SELECT COUNT(*) FROM memories") or 0
        event_rows = await conn.fetch(
            """
            SELECT id, occurred_at
            FROM events
            WHERE kind = 'CAPTURE'
            ORDER BY occurred_at ASC, id ASC
            LIMIT $1
            """,
            limit,
        )

    if dry_run:
        return {
            "mode": "dry_run",
            "capture_events_found": len(event_rows),
            "existing_memories": old_mem_count,
        }

    pipeline = SavePipeline(pool)
    replayed = 0
    errors: list[dict[str, str]] = []

    for row in event_rows:
        ev_id = row["id"]
        try:
            await pipeline.run(ev_id)
            replayed += 1
        except Exception as e:
            logger.warning(f"Replay failed for event {ev_id}: {e}")
            errors.append({"event_id": str(ev_id), "error": str(e)})

    async with pool.acquire() as conn:
        new_mem_count = await conn.fetchval("SELECT COUNT(*) FROM memories") or 0

    return {
        "mode": "replay",
        "capture_events_total": len(event_rows),
        "replayed_ok": replayed,
        "errors": errors,
        "memories_before": old_mem_count,
        "memories_after": new_mem_count,
        "delta": new_mem_count - old_mem_count,
    }
