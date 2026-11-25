"""
Tests for Per-Lane Ranking Decay Policy (Milestone L1).
Verifies:
1. Chat lane boost drops faster than work lane.
2. Code lane never decays (boost == 1.0 regardless of age).
3. Personal lane respects floor min_boost (0.90) even after 2 years.
4. Updating a memory's lane does NOT create a new version.
"""
from datetime import datetime, timezone, timedelta
import uuid
import pytest

from app.domain.lanes import compute_lane_decay_boost, update_memory_lane
from app.main import app


def test_lane_decay_curves_and_floors():
    now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    ten_days_ago = now - timedelta(days=10)
    two_years_ago = now - timedelta(days=730)

    # 1. Chat drops faster than work
    chat_boost = compute_lane_decay_boost("chat", ten_days_ago, now=now)
    work_boost = compute_lane_decay_boost("work", ten_days_ago, now=now)
    assert chat_boost < work_boost

    # 2. Code never decays
    code_boost = compute_lane_decay_boost("code", two_years_ago, now=now)
    assert code_boost == 1.0

    # 3. 2-year-old personal fact hits floor 0.90
    personal_boost = compute_lane_decay_boost("personal", two_years_ago, now=now)
    assert personal_boost == 0.90


@pytest.mark.asyncio
async def test_update_lane_does_not_create_version():
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        async with pool.acquire() as conn:
            ctx_id = await conn.fetchval(
                """
                INSERT INTO contexts (id, slug, name)
                VALUES ($1, $2, $3)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                uuid.uuid4(), f"lane-ctx-{uuid.uuid4().hex[:8]}", "Lane Test Context"
            )
            ev_id = uuid.uuid4()
            await conn.execute(
                "INSERT INTO events (id, context_id, kind, actor, payload) VALUES ($1, $2, 'SYSTEM', 'tester', '{}')",
                ev_id, ctx_id
            )
            m_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement,
                                      type, epistemic, asserted_by, confidence, lane)
                VALUES ($1, $1, $2, $3, 1, 'active', 'Use PostgreSQL for work', 'decision', 'decision', 'user', 1.0, 'chat')
                """,
                m_id, ctx_id, ev_id
            )

        res = await update_memory_lane(pool, m_id, "work")
        assert res["lane"] == "work"
        assert res["version"] == 1
