"""
Per-Lane Ranking Decay & Policy Engine (Milestone L1).
Applies ranking-only temporal decay per lane (chat, work, code, personal)
with configurable floors (min_boost). Never alters memory truth or validity.
"""
from __future__ import annotations
from datetime import datetime, timezone
import math
from typing import Any, Optional
import uuid
import asyncpg

DEFAULT_LANE_POLICIES: dict[str, dict[str, float]] = {
    "chat": {"decay_per_day": 0.05, "min_boost": 0.80},
    "work": {"decay_per_day": 0.01, "min_boost": 0.85},
    "code": {"decay_per_day": 0.0, "min_boost": 1.0},
    "personal": {"decay_per_day": 0.002, "min_boost": 0.90},
}


def compute_lane_decay_boost(
    lane: str,
    reference_time: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    policies: Optional[dict[str, dict[str, float]]] = None,
) -> float:
    """
    Computes ranking-only decay multiplier:
      boost = max(min_boost, exp(-decay_per_day * age_days))
    """
    pol_map = policies or DEFAULT_LANE_POLICIES
    pol = pol_map.get(lane, pol_map.get("personal", {"decay_per_day": 0.002, "min_boost": 0.90}))
    lam = float(pol["decay_per_day"])
    min_boost = float(pol["min_boost"])

    if lam <= 0.0 or reference_time is None:
        return 1.0

    current_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    age_days = max(0.0, (current_time - reference_time).total_seconds() / 86400.0)
    decayed = math.exp(-lam * age_days)
    return round(max(min_boost, decayed), 6)


async def load_lane_policies(conn: asyncpg.Connection) -> dict[str, dict[str, Any]]:
    """Loads lane policies from PostgreSQL with safe fallback to defaults."""
    try:
        rows = await conn.fetch(
            "SELECT lane::text, decay_per_day, min_boost, extraction_prompt, auto_commit_min_conf FROM lane_policies"
        )
        if rows:
            return {
                r["lane"]: {
                    "decay_per_day": float(r["decay_per_day"]),
                    "min_boost": float(r["min_boost"]),
                    "extraction_prompt": r["extraction_prompt"],
                    "auto_commit_min_conf": float(r["auto_commit_min_conf"]),
                }
                for r in rows
            }
    except Exception:
        pass
    return {k: dict(v) for k, v in DEFAULT_LANE_POLICIES.items()}


async def update_memory_lane(pool: asyncpg.Pool, memory_id: uuid.UUID, new_lane: str) -> dict[str, Any]:
    """
    Updates a memory's lane in place (allowed by memories_closeout_only trigger).
    Does NOT create a new version.
    """
    if new_lane not in DEFAULT_LANE_POLICIES:
        raise ValueError(f"Invalid lane '{new_lane}'. Must be one of {list(DEFAULT_LANE_POLICIES.keys())}.")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE memories
            SET lane = $2::memory_lane
            WHERE id = $1
            RETURNING id, lineage_id, version, lane::text AS lane
            """,
            memory_id,
            new_lane,
        )
        if not row:
            raise ValueError(f"Memory {memory_id} not found")
        return {
            "id": str(row["id"]),
            "lineage_id": str(row["lineage_id"]),
            "version": row["version"],
            "lane": row["lane"],
        }
