"""
Unit & Integration Tests for Milestone M0.6:
Bi-Temporal Correctness.

Verifies:
1. Event occurred_at propagation to citation said_at (e.g. occurred_at=-30d).
2. Natural language valid_from_text parsing ("since March" -> valid_from).
3. Transaction time travel (time_travel_at before update returns v1, after returns v2).
4. World time filtering (valid_at selects memory active at that world time).
"""
import uuid
from datetime import datetime, timedelta, timezone
import pytest

from app.domain.events import CaptureRequest
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.adapters.storage.pg_recall import PostgresRecallAdapter
from app.domain.apply import MemoryApplier
from app.main import app


@pytest.mark.asyncio
async def test_citation_said_at_reflects_event_occurred_at():
    """A capture with occurred_at=-30d -> citation said_at is 30 days ago."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)
        recall_adapter = PostgresRecallAdapter(pool, embedding_adapter=getattr(app.state, "embedding", None))

        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        text = "I started learning Rust for systems programming."
        cap = await events.capture(
            CaptureRequest(
                content=text,
                source={"kind": "chat", "title": "learning", "author": "user"},
                idempotency_key=f"bitemp_1_{uuid.uuid4()}",
                occurred_at=thirty_days_ago,
            )
        )

        data = {
            "entities": [
                {"ref": "e1", "name": "User", "kind": "person"},
                {"ref": "e2", "name": "Rust", "kind": "tool"},
            ],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "User started learning Rust for systems programming.",
                "subject": "e1",
                "predicate": "learns_language",
                "object": "e2",
                "cardinality": "many",
                "polarity": True,
                "evidence": ["I started learning Rust"],
                "confidence": 0.95,
                "importance": 0.7,
            }],
        }
        await applier.apply_extraction(cap.event_id, cap.context_id, text, data)

        recalled = await recall_adapter.recall("Rust systems programming", limit=5)
        assert len(recalled) > 0
        target = next((m for m in recalled if "Rust" in m["statement"]), None)
        assert target is not None

        # Verify said_at in result and citations matches thirty_days_ago
        assert target.get("said_at") is not None
        said_at_dt = datetime.fromisoformat(target["said_at"])
        assert abs((said_at_dt - thirty_days_ago).total_seconds()) < 5

        assert len(target["citations"]) > 0
        cit_said_at = datetime.fromisoformat(target["citations"][0]["said_at"])
        assert abs((cit_said_at - thirty_days_ago).total_seconds()) < 5


@pytest.mark.asyncio
async def test_valid_from_text_resolution():
    """valid_from_text='since March' resolves valid_from relative to occurred_at."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)

        base_time = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
        text = "I have lived in Kyoto since March."
        cap = await events.capture(
            CaptureRequest(
                content=text,
                source={"kind": "chat", "title": "residence", "author": "user"},
                idempotency_key=f"bitemp_2_{uuid.uuid4()}",
                occurred_at=base_time,
            )
        )

        data = {
            "entities": [
                {"ref": "e1", "name": "User", "kind": "person"},
                {"ref": "e2", "name": "Kyoto", "kind": "place"},
            ],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "User lives in Kyoto.",
                "subject": "e1",
                "predicate": "lives_in",
                "object": "e2",
                "cardinality": "one",
                "polarity": True,
                "valid_from_text": "since March",
                "evidence": ["I have lived in Kyoto since March."],
                "confidence": 0.95,
                "importance": 0.8,
            }],
        }
        res = await applier.apply_extraction(cap.event_id, cap.context_id, text, data)
        mem_id = uuid.UUID(res["memories"][0]["id"])

        async with pool.acquire() as conn:
            mem = await conn.fetchrow("SELECT valid_from, recorded_at FROM memories WHERE id = $1", mem_id)
            assert mem["valid_from"] is not None
            # March of 2026
            assert mem["valid_from"].month == 3
            assert mem["valid_from"].year == 2026


@pytest.mark.asyncio
async def test_time_travel_transaction_time():
    """time_travel_at before update returns v1, and after returns v2."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)
        recall_adapter = PostgresRecallAdapter(pool, embedding_adapter=getattr(app.state, "embedding", None))

        t1 = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        t_split = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
        t_after = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)

        # Event 1: Postgres recorded at t1
        text1 = "Lumen primary database is Postgres."
        cap1 = await events.capture(
            CaptureRequest(
                content=text1,
                source={"kind": "chat", "title": "arch", "author": "user"},
                idempotency_key=f"bitemp_tt_1_{uuid.uuid4()}",
                occurred_at=t1,
                recorded_at=t1,
            )
        )
        d1 = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m1",
                "type": "decision",
                "epistemic": "decision",
                "asserted_by": "user",
                "statement": "Lumen primary database is Postgres.",
                "subject": "e1",
                "predicate": "primary_database",
                "object_value": "Postgres",
                "cardinality": "one",
                "polarity": True,
                "evidence": ["Lumen primary database is Postgres."],
                "confidence": 0.95,
                "importance": 0.9,
            }],
        }
        res1 = await applier.apply_extraction(cap1.event_id, cap1.context_id, text1, d1)
        mem1_id = uuid.UUID(res1["memories"][0]["id"])

        # Event 2: DuckDB recorded at t2 (supersedes v1)
        text2 = "Lumen primary database is DuckDB."
        cap2 = await events.capture(
            CaptureRequest(
                content=text2,
                source={"kind": "chat", "title": "arch", "author": "user"},
                idempotency_key=f"bitemp_tt_2_{uuid.uuid4()}",
                occurred_at=t2,
                recorded_at=t2,
            )
        )
        d2 = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m2",
                "type": "decision",
                "epistemic": "decision",
                "asserted_by": "user",
                "statement": "Lumen primary database is DuckDB.",
                "subject": "e1",
                "predicate": "primary_database",
                "object_value": "DuckDB",
                "cardinality": "one",
                "polarity": True,
                "evidence": ["Lumen primary database is DuckDB."],
                "confidence": 0.95,
                "importance": 0.9,
            }],
        }
        res2 = await applier.apply_extraction(cap2.event_id, cap2.context_id, text2, d2)
        mem2_id = uuid.UUID(res2["memories"][0]["id"])

        # Time travel to t_split (between t1 and t2): should recall v1 (Postgres)
        recalled_past = await recall_adapter.recall("primary database", limit=5, time_travel_at=t_split)
        assert len(recalled_past) > 0
        past_target = next((m for m in recalled_past if "primary database" in m["statement"]), None)
        assert past_target is not None
        assert "Postgres" in past_target["statement"]
        assert past_target["version"] == 1

        # Time travel to t_after: should recall v2 (DuckDB)
        recalled_now = await recall_adapter.recall("primary database", limit=5, time_travel_at=t_after)
        assert len(recalled_now) > 0
        now_target = next((m for m in recalled_now if "primary database" in m["statement"]), None)
        assert now_target is not None
        assert "DuckDB" in now_target["statement"]
        assert now_target["version"] == 2


@pytest.mark.asyncio
async def test_valid_at_world_time_filtering():
    """valid_at filters memories based on world time validity interval."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)
        recall_adapter = PostgresRecallAdapter(pool, embedding_adapter=getattr(app.state, "embedding", None))

        v_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        v_end = datetime(2025, 1, 1, tzinfo=timezone.utc)
        t_2024 = datetime(2024, 6, 1, tzinfo=timezone.utc)
        t_2026 = datetime(2026, 1, 1, tzinfo=timezone.utc)

        # Capture memory with valid_from set directly in extraction data
        cap = await events.capture(
            CaptureRequest(
                content="I lived in London during 2024.",
                source={"kind": "chat", "title": "history", "author": "user"},
                idempotency_key=f"bitemp_val_1_{uuid.uuid4()}",
                occurred_at=datetime.now(timezone.utc),
            )
        )
        d = {
            "entities": [
                {"ref": "e1", "name": "User", "kind": "person"},
                {"ref": "e2", "name": "London", "kind": "place"},
            ],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "User lived in London.",
                "subject": "e1",
                "predicate": "lived_in",
                "object": "e2",
                "cardinality": "many",
                "polarity": True,
                "valid_from": v_start.isoformat(),
                "evidence": ["I lived in London during 2024."],
                "confidence": 0.95,
                "importance": 0.5,
            }],
        }
        res = await applier.apply_extraction(cap.event_id, cap.context_id, "I lived in London during 2024.", d)
        m_id = uuid.UUID(res["memories"][0]["id"])

        # Set valid_to once via the closeout trigger (valid_to is in free list)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE memories SET valid_to = $1 WHERE id = $2",
                v_end,
                m_id,
            )

        # 1. Query with valid_at inside interval (2024-06-01) -> recalls memory
        rec_inside = await recall_adapter.recall("lived London", limit=5, valid_at=t_2024)
        assert any(m["id"] == str(m_id) for m in rec_inside)

        # 2. Query with valid_at outside interval (2026-01-01) -> does not recall memory
        rec_outside = await recall_adapter.recall("lived London", limit=5, valid_at=t_2026)
        assert not any(m["id"] == str(m_id) for m in rec_outside)
