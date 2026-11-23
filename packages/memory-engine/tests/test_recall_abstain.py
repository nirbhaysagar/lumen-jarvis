"""
Unit & Integration Tests for Milestone M0.7:
Recall Honesty and Abstention.

Verifies:
1. Empty DB -> status='abstain', abstain_reason='no_candidates'.
2. Unrelated memory in DB -> query with no match abstains.
3. Temporal channel never adds an ID that no content channel nominated.
4. pending_review rows are never returned by recall (current_memories view isolation).
5. retrieval_traces row is written on every recall.
"""
import uuid
from datetime import datetime, timezone
import pytest

from app.domain.events import CaptureRequest
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.adapters.storage.pg_recall import PostgresRecallAdapter
from app.domain.apply import MemoryApplier
from app.main import app


@pytest.mark.asyncio
async def test_empty_db_abstains_no_candidates():
    """An empty DB returns status='abstain' with abstain_reason='no_candidates'."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        recall_adapter = PostgresRecallAdapter(pool, embedding_adapter=getattr(app.state, "embedding", None))

        res = await recall_adapter.recall("What is my blood group?", limit=5)
        assert res.status == "abstain"
        assert res.abstain_reason == "no_candidates"
        assert len(res) == 0

        # Verify retrieval_traces was written
        async with pool.acquire() as conn:
            trace = await conn.fetchrow("SELECT * FROM retrieval_traces WHERE id = $1", res.trace_id)
            assert trace is not None
            assert trace["query"] == "What is my blood group?"
            assert trace["returned"] == []


@pytest.mark.asyncio
async def test_unrelated_memory_abstains():
    """Unrelated memories ('I use Neovim') do not match 'what is my blood group?' -> abstain."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)
        recall_adapter = PostgresRecallAdapter(pool, embedding_adapter=getattr(app.state, "embedding", None))

        text = "I use Neovim for editing all my code."
        cap = await events.capture(
            CaptureRequest(
                content=text,
                source={"kind": "chat", "title": "editor", "author": "user"},
                idempotency_key=f"abstain_unrel_{uuid.uuid4()}",
            )
        )
        d = {
            "entities": [
                {"ref": "e1", "name": "User", "kind": "person"},
                {"ref": "e2", "name": "Neovim", "kind": "tool"},
            ],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "User uses Neovim for code editing.",
                "subject": "e1",
                "predicate": "uses_editor",
                "object": "e2",
                "cardinality": "many",
                "polarity": True,
                "evidence": ["I use Neovim for editing"],
                "confidence": 0.95,
                "importance": 0.6,
            }],
        }
        await applier.apply_extraction(cap.event_id, cap.context_id, text, d)

        # Unrelated query
        res = await recall_adapter.recall("What is my blood group?", limit=5)
        assert res.status == "abstain"
        assert res.abstain_reason == "no_candidates"
        assert len(res) == 0


@pytest.mark.asyncio
async def test_temporal_channel_never_adds_unrelated_memory():
    """Temporal decay channel only boosts existing candidates, never nominates new ones."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)
        recall_adapter = PostgresRecallAdapter(pool, embedding_adapter=getattr(app.state, "embedding", None))

        # Recent memory inserted right now
        text = "Lumen primary database is Postgres 17."
        cap = await events.capture(
            CaptureRequest(
                content=text,
                source={"kind": "chat", "title": "db", "author": "user"},
                idempotency_key=f"abstain_temp_{uuid.uuid4()}",
            )
        )
        d = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "Lumen primary database is Postgres 17.",
                "subject": "e1",
                "predicate": "primary_database",
                "object_value": "Postgres 17",
                "cardinality": "one",
                "polarity": True,
                "evidence": ["Lumen primary database is Postgres 17."],
                "confidence": 0.95,
                "importance": 1.0,  # Maximum importance and recency
            }],
        }
        await applier.apply_extraction(cap.event_id, cap.context_id, text, d)

        # Query completely unrelated to Postgres/database
        res = await recall_adapter.recall("Who won the 1994 World Cup tournament?", limit=5)
        assert res.status == "abstain"
        assert len(res) == 0


@pytest.mark.asyncio
async def test_pending_review_rows_not_returned():
    """Memories with status='pending_review' are excluded from recall."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        recall_adapter = PostgresRecallAdapter(pool, embedding_adapter=getattr(app.state, "embedding", None))

        cap = await events.capture(
            CaptureRequest(
                content="My blood group is O positive.",
                source={"kind": "chat", "title": "bio", "author": "user"},
                idempotency_key=f"abstain_pend_{uuid.uuid4()}",
            )
        )

        now = datetime.now(timezone.utc)
        mem_id = uuid.uuid4()
        lineage_id = uuid.uuid4()

        # Insert directly as pending_review (unverified claim)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memories (
                    id, lineage_id, version, type, epistemic, asserted_by,
                    statement, cardinality, polarity, confidence, importance,
                    context_id, status, created_event, recorded_at, valid_from
                )
                VALUES ($1, $2, 1, 'fact', 'belief', 'user',
                        'User blood group is O positive.', 'one', true, 0.4, 0.8,
                        $3, 'pending_review', $4, $5, $5)
                """,
                mem_id,
                lineage_id,
                cap.context_id,
                cap.event_id,
                now,
            )

            # Confirm it is visible in pending_memories view, but NOT in current_memories view
            pending_row = await conn.fetchrow("SELECT * FROM pending_memories WHERE id = $1", mem_id)
            assert pending_row is not None
            current_row = await conn.fetchrow("SELECT * FROM current_memories WHERE id = $1", mem_id)
            assert current_row is None

        # Recall should abstain because current_memories does not contain it
        res = await recall_adapter.recall("blood group", limit=5)
        assert res.status == "abstain"
        assert len(res) == 0
