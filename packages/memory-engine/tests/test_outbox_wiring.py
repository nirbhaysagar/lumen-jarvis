"""
Unit & Integration Tests for Milestone M0.8:
Outbox Wiring, Atomicity, and Transactional Provenance.

Verifies:
1. Extraction writes outbox rows (memory.created, entity.upserted) and DERIVED events.
2. A forced rollback leaves zero outbox rows and zero memories.
3. Superseding an existing memory emits memory.superseded and memory.created outbox records.
4. /v1/outbox/stats and /v1/outbox/pending reflect queued records; /v1/outbox/{id}/ack marks processed.
"""
import json
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from app.core.config import settings
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.domain.events import CaptureRequest
from app.domain.apply import MemoryApplier
from app.main import app


@pytest.mark.asyncio
async def test_extract_writes_outbox_and_derived_events():
    """Verify applying extraction inserts outbox records and DERIVED events in the same transaction."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)

        content = "Lumen runs on local Apple Silicon hardware."
        cap = await events.capture(
            CaptureRequest(
                content=content,
                source={"kind": "chat", "title": "hardware", "author": "user"},
                idempotency_key=f"outbox_test_{uuid.uuid4()}",
            )
        )

        extraction_data = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "Lumen runs on local Apple Silicon hardware.",
                "subject": "e1",
                "predicate": "runs_on",
                "object_value": "Apple Silicon",
                "cardinality": "one",
                "polarity": True,
                "evidence": ["Lumen runs on local Apple Silicon hardware."],
                "confidence": 1.0,
                "importance": 0.8,
            }],
        }

        res = await applier.apply_extraction(cap.event_id, cap.context_id, content, extraction_data)
        assert res["memories_created"] == 1
        mem_id = uuid.UUID(res["memories"][0]["id"])

        async with pool.acquire() as conn:
            # Check outbox has memory.created
            ob_rows = await conn.fetch(
                "SELECT * FROM outbox WHERE event_id = $1 AND topic = 'memory.created'",
                cap.event_id,
            )
            assert len(ob_rows) >= 1
            payload = json.loads(ob_rows[0]["payload"]) if isinstance(ob_rows[0]["payload"], str) else ob_rows[0]["payload"]
            assert payload["memory_id"] == str(mem_id)
            assert ob_rows[0]["status"] == "queued"

            # Check DERIVED event exists in events table
            der_event = await conn.fetchrow(
                "SELECT * FROM events WHERE kind = 'DERIVED' AND type = 'memory.created' AND target_id = $1",
                mem_id,
            )
            assert der_event is not None
            assert cap.event_id in der_event["caused_by"]


@pytest.mark.asyncio
async def test_forced_rollback_leaves_no_outbox_rows():
    """Verify that if a transaction rolls back, no outbox rows or memories remain."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)

        content = "Temporary memory that will be aborted."
        cap = await events.capture(
            CaptureRequest(
                content=content,
                source={"kind": "chat", "title": "aborted", "author": "user"},
                idempotency_key=f"outbox_abort_{uuid.uuid4()}",
            )
        )

        dummy_mem_id = uuid.uuid4()
        dummy_lineage = uuid.uuid4()

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO memories (
                            id, lineage_id, version, type, epistemic, asserted_by,
                            statement, cardinality, polarity, confidence, importance,
                            context_id, status, created_event
                        ) VALUES ($1, $2, 1, 'fact', 'fact', 'user', 'Aborted test statement', 'one', true, 0.9, 0.5, $3, 'active', $4)
                        """,
                        dummy_mem_id,
                        dummy_lineage,
                        cap.context_id,
                        cap.event_id,
                    )
                    await conn.execute(
                        """
                        INSERT INTO outbox (event_id, topic, payload, status)
                        VALUES ($1, 'memory.created', $2, 'queued')
                        """,
                        cap.event_id,
                        json.dumps({"memory_id": str(dummy_mem_id)}),
                    )
                    # Force deliberate failure
                    raise RuntimeError("Simulated transaction crash!")
        except RuntimeError:
            pass

        async with pool.acquire() as conn:
            mem_row = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", dummy_mem_id)
            assert mem_row is None

            ob_row = await conn.fetchrow("SELECT * FROM outbox WHERE event_id = $1 AND topic = 'memory.created'", cap.event_id)
            assert ob_row is None


@pytest.mark.asyncio
async def test_supersede_emits_superseded_and_created_outbox():
    """Verify supersede writes memory.superseded and memory.created outbox entries."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)

        # 1. Version 1
        cap1 = await events.capture(
            CaptureRequest(
                content="Lumen uses Redis 7.",
                source={"kind": "chat", "title": "v1", "author": "user"},
                idempotency_key=f"super_ob_1_{uuid.uuid4()}",
            )
        )
        ext1 = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "Lumen uses Redis 7.",
                "subject": "e1",
                "predicate": "cache_system",
                "object_value": "Redis 7",
                "cardinality": "one",
                "polarity": True,
                "evidence": ["Lumen uses Redis 7."],
                "confidence": 1.0,
                "importance": 0.8,
            }],
        }
        res1 = await applier.apply_extraction(cap1.event_id, cap1.context_id, "Lumen uses Redis 7.", ext1)
        v1_id = uuid.UUID(res1["memories"][0]["id"])

        # 2. Version 2 (supersedes v1)
        cap2 = await events.capture(
            CaptureRequest(
                content="Lumen now uses Dragonfly instead of Redis.",
                source={"kind": "chat", "title": "v2", "author": "user"},
                idempotency_key=f"super_ob_2_{uuid.uuid4()}",
            )
        )
        ext2 = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m2",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "Lumen uses Dragonfly.",
                "subject": "e1",
                "predicate": "cache_system",
                "object_value": "Dragonfly",
                "cardinality": "one",
                "polarity": True,
                "evidence": ["Lumen now uses Dragonfly"],
                "confidence": 1.0,
                "importance": 0.8,
            }],
        }
        res2 = await applier.apply_extraction(cap2.event_id, cap2.context_id, "Lumen now uses Dragonfly instead of Redis.", ext2)
        v2_id = uuid.UUID(res2["memories"][0]["id"])

        async with pool.acquire() as conn:
            # Verify memory.superseded outbox row for v1
            super_row = await conn.fetchrow(
                "SELECT * FROM outbox WHERE event_id = $1 AND topic = 'memory.superseded'",
                cap2.event_id,
            )
            assert super_row is not None
            super_payload = json.loads(super_row["payload"]) if isinstance(super_row["payload"], str) else super_row["payload"]
            assert super_payload["memory_id"] == str(v1_id)
            assert super_payload["superseded_by"] == str(v2_id)

            # Verify memory.created outbox row for v2
            create_row = await conn.fetchrow(
                "SELECT * FROM outbox WHERE event_id = $1 AND topic = 'memory.created'",
                cap2.event_id,
            )
            assert create_row is not None
            create_payload = json.loads(create_row["payload"]) if isinstance(create_row["payload"], str) else create_row["payload"]
            assert create_payload["memory_id"] == str(v2_id)


@pytest.mark.asyncio
async def test_outbox_stats_and_ack_endpoints():
    """Verify /v1/outbox/stats, /v1/outbox/pending, and /v1/outbox/{id}/ack."""
    headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Fetch stats
            stats_resp = await client.get("/v1/outbox/stats", headers=headers)
            assert stats_resp.status_code == 200
            stats_data = stats_resp.json()
            assert "queued" in stats_data
            assert "processed" in stats_data

            # 2. Fetch pending items
            pending_resp = await client.get("/v1/outbox/pending?limit=10", headers=headers)
            assert pending_resp.status_code == 200
            items = pending_resp.json().get("items", [])
            if items:
                first_id = int(items[0]["id"])
                # 3. Ack item
                ack_resp = await client.post(f"/v1/outbox/{first_id}/ack", headers=headers)
                assert ack_resp.status_code == 200
                assert ack_resp.json()["status"] == "processed"
