"""
Unit & Integration Tests for Milestone M0.5:
Supersede Scoped by Speaker and Polarity.

Covers:
1. Same speaker with cardinality='one' -> supersedes to v2 in same lineage.
2. Different speaker -> never supersedes across speakers, creates distinct lineage,
   CONTRADICTS edge, review_queue(kind='contradiction') row, and claims row.
3. Negation (polarity=False) supersedes active positive memory from same speaker.
4. Many-valued preferences (cardinality='many') never supersede.
"""
import uuid
import pytest
from app.domain.events import CaptureRequest
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.domain.apply import MemoryApplier
from app.main import app


@pytest.mark.asyncio
async def test_same_speaker_cardinality_one_supersedes_to_v2():
    """Same speaker, cardinality=one -> v2 with SUPERSEDES relation."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)

        # Event 1: User sets database to Postgres
        t1 = "Lumen primary database is Postgres."
        cap1 = await events.capture(
            CaptureRequest(
                content=t1,
                source={"kind": "chat", "title": "architecture", "author": "user"},
                idempotency_key=f"same_spk_1_{uuid.uuid4()}",
            )
        )
        d1 = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "Lumen primary database is Postgres.",
                "subject": "e1",
                "predicate": "primary_database",
                "object_value": "Postgres",
                "cardinality": "one",
                "polarity": True,
                "evidence": ["Lumen primary database is Postgres."],
                "confidence": 0.95,
                "importance": 0.8,
            }],
        }
        res1 = await applier.apply_extraction(cap1.event_id, cap1.context_id, t1, d1)
        mem1_id = uuid.UUID(res1["memories"][0]["id"])
        lineage1_id = uuid.UUID(res1["memories"][0]["lineage_id"])

        # Event 2: User updates database to DuckDB
        t2 = "Lumen primary database is DuckDB."
        cap2 = await events.capture(
            CaptureRequest(
                content=t2,
                source={"kind": "chat", "title": "architecture", "author": "user"},
                idempotency_key=f"same_spk_2_{uuid.uuid4()}",
            )
        )
        d2 = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m2",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "Lumen primary database is DuckDB.",
                "subject": "e1",
                "predicate": "primary_database",
                "object_value": "DuckDB",
                "cardinality": "one",
                "polarity": True,
                "evidence": ["Lumen primary database is DuckDB."],
                "confidence": 0.95,
                "importance": 0.8,
            }],
        }
        res2 = await applier.apply_extraction(cap2.event_id, cap2.context_id, t2, d2)
        mem2_id = uuid.UUID(res2["memories"][0]["id"])

        async with pool.acquire() as conn:
            # Check v1: superseded
            m1 = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", mem1_id)
            assert m1["version"] == 1
            assert m1["superseded_at"] is not None
            assert m1["superseded_by"] == mem2_id
            assert m1["status"] == "active"

            # Check v2: active, same lineage, version 2
            m2 = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", mem2_id)
            assert m2["version"] == 2
            assert m2["lineage_id"] == lineage1_id
            assert m2["superseded_at"] is None
            assert m2["superseded_by"] is None

            # Check SUPERSEDES relation
            rel = await conn.fetchrow(
                "SELECT * FROM memory_relations WHERE from_memory = $1 AND to_memory = $2 AND type = 'SUPERSEDES'",
                mem2_id,
                mem1_id,
            )
            assert rel is not None


@pytest.mark.asyncio
async def test_different_speaker_does_not_supersede_creates_contradiction():
    """
    A different speaker statement on the same (subject, predicate) never overwrites.
    Creates two separate lineages, a CONTRADICTS relation, and stages in review_queue.
    Third party statement is downgraded to epistemic='belief' and stored in claims.
    """
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)

        # Event 1: User says Lumen uses Postgres
        t1 = "Lumen primary database is Postgres."
        cap1 = await events.capture(
            CaptureRequest(
                content=t1,
                source={"kind": "chat", "title": "user statement", "author": "user"},
                idempotency_key=f"diff_spk_1_{uuid.uuid4()}",
            )
        )
        d1 = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "Lumen primary database is Postgres.",
                "subject": "e1",
                "predicate": "primary_database",
                "object_value": "Postgres",
                "cardinality": "one",
                "polarity": True,
                "evidence": ["Lumen primary database is Postgres."],
                "confidence": 0.95,
                "importance": 0.8,
            }],
        }
        res1 = await applier.apply_extraction(cap1.event_id, cap1.context_id, t1, d1)
        mem1_id = uuid.UUID(res1["memories"][0]["id"])
        lineage1_id = uuid.UUID(res1["memories"][0]["lineage_id"])

        # Event 2: Rahul asserts Lumen primary database is MongoDB
        t2 = "Rahul said Lumen primary database is MongoDB."
        cap2 = await events.capture(
            CaptureRequest(
                content=t2,
                source={"kind": "chat", "title": "rahul statement", "author": "Rahul"},
                idempotency_key=f"diff_spk_2_{uuid.uuid4()}",
            )
        )
        d2 = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m2",
                "type": "fact",
                "epistemic": "fact",  # Raw extraction says fact, should be downgraded to belief
                "asserted_by": "Rahul",
                "statement": "Rahul said Lumen primary database is MongoDB.",
                "subject": "e1",
                "predicate": "primary_database",
                "object_value": "MongoDB",
                "cardinality": "one",
                "polarity": True,
                "evidence": ["Rahul said Lumen primary database is MongoDB."],
                "confidence": 0.9,
                "importance": 0.7,
            }],
        }
        res2 = await applier.apply_extraction(cap2.event_id, cap2.context_id, t2, d2)
        mem2_id = uuid.UUID(res2["memories"][0]["id"])
        lineage2_id = uuid.UUID(res2["memories"][0]["lineage_id"])

        async with pool.acquire() as conn:
            # 1. User memory is NOT superseded
            m1 = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", mem1_id)
            assert m1["superseded_at"] is None
            assert m1["superseded_by"] is None
            assert m1["version"] == 1
            assert m1["epistemic"] == "fact"

            # 2. Rahul memory has distinct lineage and version 1
            m2 = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", mem2_id)
            assert m2["lineage_id"] != lineage1_id
            assert m2["version"] == 1
            assert m2["superseded_at"] is None
            # Epistemic downgraded to belief
            assert m2["epistemic"] == "belief"

            # 3. Third-party claim recorded in claims table
            claim = await conn.fetchrow("SELECT * FROM claims WHERE memory_id = $1", mem2_id)
            assert claim is not None
            assert claim["claimant"] == "Rahul"
            assert claim["stance"] == "supports"

            # 4. CONTRADICTS relation created
            rel = await conn.fetchrow(
                "SELECT * FROM memory_relations WHERE from_memory = $1 AND to_memory = $2 AND type = 'CONTRADICTS'",
                mem2_id,
                mem1_id,
            )
            assert rel is not None

            # 5. Staged in review_queue with kind='contradiction'
            rq = await conn.fetchrow(
                "SELECT * FROM review_queue WHERE kind = 'contradiction' AND $1 = ANY(memory_ids) AND $2 = ANY(memory_ids)",
                mem1_id,
                mem2_id,
            )
            assert rq is not None
            assert rq["status"] == "open"


@pytest.mark.asyncio
async def test_negation_supersedes_positive_memory():
    """Polarity=False memory from same speaker supersedes active Polarity=True memory."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)

        # Event 1: User uses Docker
        t1 = "I use Docker for deployment."
        cap1 = await events.capture(
            CaptureRequest(
                content=t1,
                source={"kind": "chat", "title": "tools", "author": "user"},
                idempotency_key=f"neg_1_{uuid.uuid4()}",
            )
        )
        d1 = {
            "entities": [
                {"ref": "e1", "name": "User", "kind": "person"},
                {"ref": "e2", "name": "Docker", "kind": "tool"},
            ],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "User uses Docker for deployment.",
                "subject": "e1",
                "predicate": "uses_tool",
                "object": "e2",
                "cardinality": "many",
                "polarity": True,
                "evidence": ["I use Docker for deployment."],
                "confidence": 0.9,
                "importance": 0.6,
            }],
        }
        res1 = await applier.apply_extraction(cap1.event_id, cap1.context_id, t1, d1)
        mem1_id = uuid.UUID(res1["memories"][0]["id"])
        lineage1_id = uuid.UUID(res1["memories"][0]["lineage_id"])

        # Event 2: User says "I do not use Docker anymore"
        t2 = "I do not use Docker anymore for deployment."
        cap2 = await events.capture(
            CaptureRequest(
                content=t2,
                source={"kind": "chat", "title": "tools", "author": "user"},
                idempotency_key=f"neg_2_{uuid.uuid4()}",
            )
        )
        d2 = {
            "entities": [
                {"ref": "e1", "name": "User", "kind": "person"},
                {"ref": "e2", "name": "Docker", "kind": "tool"},
            ],
            "memories": [{
                "ref": "m2",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "User does not use Docker for deployment.",
                "subject": "e1",
                "predicate": "uses_tool",
                "object": "e2",
                "cardinality": "many",
                "polarity": False,  # Negation!
                "evidence": ["I do not use Docker anymore"],
                "confidence": 0.95,
                "importance": 0.7,
            }],
        }
        res2 = await applier.apply_extraction(cap2.event_id, cap2.context_id, t2, d2)
        mem2_id = uuid.UUID(res2["memories"][0]["id"])

        async with pool.acquire() as conn:
            # Positive memory was superseded
            m1 = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", mem1_id)
            assert m1["superseded_at"] is not None
            assert m1["superseded_by"] == mem2_id
            assert m1["version"] == 1

            # Negation memory is active v2 in same lineage
            m2 = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", mem2_id)
            assert m2["lineage_id"] == lineage1_id
            assert m2["version"] == 2
            assert m2["polarity"] is False
            assert m2["superseded_at"] is None

            # SUPERSEDES relation present
            rel = await conn.fetchrow(
                "SELECT * FROM memory_relations WHERE from_memory = $1 AND to_memory = $2 AND type = 'SUPERSEDES'",
                mem2_id,
                mem1_id,
            )
            assert rel is not None


@pytest.mark.asyncio
async def test_many_valued_preferences_never_supersede():
    """Many-valued preferences with different objects never supersede each other."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)

        t1 = "I prefer Neovim for editing."
        cap1 = await events.capture(
            CaptureRequest(
                content=t1,
                source={"kind": "chat", "title": "prefs", "author": "user"},
                idempotency_key=f"pref_1_{uuid.uuid4()}",
            )
        )
        d1 = {
            "entities": [
                {"ref": "e1", "name": "User", "kind": "person"},
                {"ref": "e2", "name": "Neovim", "kind": "tool"},
            ],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "User prefers Neovim for editing.",
                "subject": "e1",
                "predicate": "prefers",
                "object": "e2",
                "cardinality": "many",
                "polarity": True,
                "evidence": ["I prefer Neovim for editing."],
                "confidence": 0.9,
                "importance": 0.5,
            }],
        }
        res1 = await applier.apply_extraction(cap1.event_id, cap1.context_id, t1, d1)
        mem1_id = uuid.UUID(res1["memories"][0]["id"])

        t2 = "I prefer Postgres for databases."
        cap2 = await events.capture(
            CaptureRequest(
                content=t2,
                source={"kind": "chat", "title": "prefs", "author": "user"},
                idempotency_key=f"pref_2_{uuid.uuid4()}",
            )
        )
        d2 = {
            "entities": [
                {"ref": "e1", "name": "User", "kind": "person"},
                {"ref": "e3", "name": "Postgres", "kind": "tool"},
            ],
            "memories": [{
                "ref": "m2",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "User prefers Postgres for databases.",
                "subject": "e1",
                "predicate": "prefers",
                "object": "e3",
                "cardinality": "many",
                "polarity": True,
                "evidence": ["I prefer Postgres for databases."],
                "confidence": 0.9,
                "importance": 0.5,
            }],
        }
        res2 = await applier.apply_extraction(cap2.event_id, cap2.context_id, t2, d2)
        mem2_id = uuid.UUID(res2["memories"][0]["id"])

        async with pool.acquire() as conn:
            m1 = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", mem1_id)
            m2 = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", mem2_id)

            # Both remain active, version 1, different lineages
            assert m1["superseded_at"] is None
            assert m2["superseded_at"] is None
            assert m1["version"] == 1
            assert m2["version"] == 1
            assert m1["lineage_id"] != m2["lineage_id"]

            # No contradiction or review queue items
            rq_count = await conn.fetchval("SELECT count(*) FROM review_queue")
            assert rq_count == 0
            rel_count = await conn.fetchval(
                "SELECT count(*) FROM memory_relations WHERE type IN ('SUPERSEDES', 'CONTRADICTS')"
            )
            assert rel_count == 0
