"""
Unit & Integration Tests for Glass-Box Live Memory Viewer API & UI (Phase 1).
Verifies:
1. Public GET /viewer serves self-contained interactive web UI.
2. GET /v1/viewer/stats returns dashboard totals and breakdown.
3. GET /v1/viewer/memories returns memories with filtering, search, and citations.
4. GET /v1/viewer/memories/{lineage_id}/history returns version chain.
5. PATCH /v1/viewer/memories/{id} manually corrects memory (creates v2, supersedes v1, outbox + DERIVED audit event).
6. DELETE /v1/viewer/memories/{id} tombstones memory (status=tombstoned, valid_to=now, outbox + DERIVED event).
7. GET /v1/viewer/review-queue and POST /v1/viewer/review-queue/{id}/resolve approves/rejects staged proposals.
8. GET /v1/viewer/traces returns recent retrieval traces with RRF channel breakdown.
"""
from datetime import datetime, timezone
import json
import uuid
import pytest
from unittest.mock import AsyncMock
from httpx import AsyncClient

from app.domain.events import CaptureRequest, SourceInfo
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.domain.extraction import MemoryExtractor
from app.pipeline import SavePipeline
from app.main import app


@pytest.mark.asyncio
async def test_viewer_html_ui_public(client: AsyncClient):
    """GET /viewer must return 200 and serve the Glass-Box HTML interface."""
    resp = await client.get("/viewer")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Lumen Memory Viewer" in resp.text
    assert "Phase 1 • Glass-Box" in resp.text
    assert "Live Memory Explorer" in resp.text


@pytest.mark.asyncio
async def test_viewer_stats_endpoint(client: AsyncClient):
    """GET /v1/viewer/stats returns system statistics."""
    resp = await client.get("/v1/viewer/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "active_memories" in data
    assert "lineages" in data
    assert "pending_reviews" in data
    assert "outbox_pending" in data
    assert "lanes" in data
    assert "sensitivities" in data


@pytest.mark.asyncio
async def test_viewer_memories_crud_and_history(client: AsyncClient):
    """End-to-end test for listing, searching, diffing, correcting, and tombstoning memories."""
    pool = app.state.storage.pool
    events_adapter = PostgresEventsAdapter(pool)
    ctx_slug = f"ctx_viewer_{uuid.uuid4().hex[:8]}"

    # 1. Capture event
    cap_req = CaptureRequest(
        content="Alice lives in Indiranagar, Bengaluru and works at Cerebro Dynamics.",
        source=SourceInfo(kind="chat", title="Viewer Test Chat", author="alice"),
        context=ctx_slug,
    )
    cap_res = await events_adapter.capture(cap_req)

    # Mock extractor for fast deterministic extraction
    mock_extractor = MemoryExtractor()
    mock_extractor.extract = AsyncMock(
        return_value={
            "entities": [
                {"ref": "e1", "name": "Indiranagar", "kind": "place", "aliases": []},
                {"ref": "e2", "name": "Cerebro Dynamics", "kind": "org", "aliases": []},
            ],
            "memories": [
                {
                    "ref": "m1",
                    "type": "fact",
                    "epistemic": "fact",
                    "asserted_by": "alice",
                    "statement": "Alice lives in Indiranagar, Bengaluru.",
                    "subject": None,
                    "predicate": "lives_in",
                    "object": "e1",
                    "object_value": "Indiranagar, Bengaluru",
                    "evidence": ["Alice lives in Indiranagar, Bengaluru"],
                    "confidence": 0.95,
                    "importance": 0.8,
                    "lane": "personal",
                    "sensitivity": "normal",
                    "about_speaker": True,
                }
            ],
        }
    )

    pipeline = SavePipeline(pool, extractor=mock_extractor)
    save_report = await pipeline.run(cap_res.event_id)
    assert len(save_report.memories) > 0

    # 2. Test GET /v1/viewer/memories
    resp = await client.get("/v1/viewer/memories?status=active")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    item = data["items"][0]
    mem_id = item["id"]
    lineage_id = item["lineage_id"]
    assert item["version"] == 1
    assert item["status"] == "active"
    assert len(item["citations"]) > 0

    # Test search filter
    search_resp = await client.get("/v1/viewer/memories?search=Bengaluru")
    assert search_resp.status_code == 200
    assert any("Bengaluru" in m["statement"] for m in search_resp.json()["items"])

    # 3. Test GET /v1/viewer/memories/{lineage_id}/history
    hist_resp = await client.get(f"/v1/viewer/memories/{lineage_id}/history")
    assert hist_resp.status_code == 200
    hist_data = hist_resp.json()
    assert hist_data["lineage_id"] == lineage_id
    assert hist_data["version_count"] == 1
    assert hist_data["versions"][0]["version"] == 1

    # 4. Test PATCH /v1/viewer/memories/{mem_id} (Manual correction)
    corrected_statement = "Alice lives in Koramangala, Bengaluru."
    patch_resp = await client.patch(
        f"/v1/viewer/memories/{mem_id}",
        json={
            "statement": corrected_statement,
            "lane": "work",
            "sensitivity": "normal",
            "confidence": 0.95,
        },
    )
    assert patch_resp.status_code == 200
    patch_data = patch_resp.json()
    new_mem_id = patch_data["id"]
    assert new_mem_id != mem_id
    assert patch_data["lineage_id"] == lineage_id
    assert patch_data["version"] == 2
    assert patch_data["statement"] == corrected_statement
    assert patch_data["lane"] == "work"
    assert patch_data["superseded_id"] == mem_id

    # Verify database state after correction
    async with pool.acquire() as conn:
        old_mem = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", uuid.UUID(mem_id))
        assert old_mem["status"] == "superseded"
        assert str(old_mem["superseded_by"]) == new_mem_id
        assert old_mem["superseded_at"] is not None
        assert old_mem["valid_to"] is not None

        new_mem = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", uuid.UUID(new_mem_id))
        assert new_mem["status"] == "active"
        assert new_mem["version"] == 2
        assert new_mem["statement"] == corrected_statement
        assert new_mem["lane"] == "work"

        # Check DERIVED event and outbox
        ev = await conn.fetchrow("SELECT * FROM events WHERE type = 'memory.corrected' AND target_id = $1", uuid.UUID(new_mem_id))
        assert ev is not None
        ob = await conn.fetchrow("SELECT * FROM outbox WHERE topic = 'memory.corrected' AND event_id = $1", ev["id"])
        assert ob is not None

    # Check history reflects v1 -> v2
    hist_resp2 = await client.get(f"/v1/viewer/memories/{lineage_id}/history")
    assert hist_resp2.status_code == 200
    h_data2 = hist_resp2.json()
    assert h_data2["version_count"] == 2
    assert h_data2["versions"][0]["version"] == 1
    assert h_data2["versions"][1]["version"] == 2
    assert h_data2["versions"][1]["previous_statement"] == h_data2["versions"][0]["statement"]

    # 5. Test DELETE /v1/viewer/memories/{new_mem_id} (Manual forget / tombstone)
    del_resp = await client.delete(f"/v1/viewer/memories/{new_mem_id}")
    assert del_resp.status_code == 200
    del_data = del_resp.json()
    assert del_data["status"] == "retracted"

    # Verify database state after tombstone
    async with pool.acquire() as conn:
        tomb_mem = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", uuid.UUID(new_mem_id))
        assert tomb_mem["status"] == "retracted"
        assert tomb_mem["valid_to"] is not None

        # Check DERIVED tombstone event and outbox
        ev_tomb = await conn.fetchrow("SELECT * FROM events WHERE type = 'memory.tombstoned' AND target_id = $1", uuid.UUID(new_mem_id))
        assert ev_tomb is not None
        assert ev_tomb["tombstone"] is True
        ob_tomb = await conn.fetchrow("SELECT * FROM outbox WHERE topic = 'memory.tombstoned' AND event_id = $1", ev_tomb["id"])
        assert ob_tomb is not None


@pytest.mark.asyncio
async def test_viewer_review_queue_and_resolution(client: AsyncClient):
    """Verifies review queue listing and human adjudication resolution."""
    pool = app.state.storage.pool
    events_adapter = PostgresEventsAdapter(pool)

    # Ingest a base event
    cap_req = CaptureRequest(
        content="Bob prefers tabs over spaces.",
        source=SourceInfo(kind="chat", title="Pref Chat", author="bob"),
        context="ctx_review_test",
    )
    cap_res = await events_adapter.capture(cap_req)
    ev_id = cap_res.event_id

    # Insert a staged Tier B memory and review_queue item
    mem_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        # Get context_id
        ctx_id = await conn.fetchval("SELECT context_id FROM events WHERE id = $1", ev_id)
        await conn.execute(
            """
            INSERT INTO memories (
                id, lineage_id, version, type, epistemic, statement,
                cardinality, polarity, confidence, importance, status,
                context_id, created_event, asserted_by, lane, sensitivity, recorded_at
            )
            VALUES (
                $1, $1, 1, 'fact', 'belief', 'Bob prefers spaces over tabs.',
                'many', true, 0.65, 0.5, 'pending_review',
                $2, $3, 'speaker_2', 'code', 'normal', $4
            )
            """,
            mem_id,
            ctx_id,
            ev_id,
            now,
        )

        proposal_data = {
            "memory_id": str(mem_id),
            "statement": "Bob prefers spaces over tabs.",
            "conflict": "Cross-speaker conflict detected.",
        }
        rq_id = await conn.fetchval(
            """
            INSERT INTO review_queue (kind, reason, proposal, memory_ids, status, created_event)
            VALUES ('contradiction', 'Speaker 2 contradicts Bob preference', $1, $2, 'open', $3)
            RETURNING id
            """,
            json.dumps(proposal_data),
            [mem_id],
            ev_id,
        )

    # 1. List review queue
    rq_resp = await client.get("/v1/viewer/review-queue?status=open")
    assert rq_resp.status_code == 200
    items = rq_resp.json()
    assert len(items) >= 1
    found = next((x for x in items if x["id"] == str(rq_id)), None)
    assert found is not None
    assert found["kind"] == "contradiction"
    assert found["memory_id"] == str(mem_id)

    # 2. Resolve review proposal (Approve)
    res_resp = await client.post(
        f"/v1/viewer/review-queue/{rq_id}/resolve",
        json={"decision": "approve", "notes": "Verified preference shift"},
    )
    assert res_resp.status_code == 200
    res_data = res_resp.json()
    assert res_data["status"] == "accepted"
    assert res_data["decision"] == "approve"

    # Verify memory is now active
    async with pool.acquire() as conn:
        m_row = await conn.fetchrow("SELECT status FROM memories WHERE id = $1", mem_id)
        assert m_row["status"] == "active"
        rq_row = await conn.fetchrow("SELECT status FROM review_queue WHERE id = $1", rq_id)
        assert rq_row["status"] == "accepted"


@pytest.mark.asyncio
async def test_viewer_traces(client: AsyncClient):
    """Verifies retrieval traces are captured and retrievable via GET /v1/viewer/traces."""
    pool = app.state.storage.pool
    events_adapter = PostgresEventsAdapter(pool)

    # Ingest a memory
    cap_req = CaptureRequest(
        content="The server runs on port 8080 by default.",
        source=SourceInfo(kind="file", title="server_doc", author="dev"),
        context="ctx_trace_test",
    )
    cap_res = await events_adapter.capture(cap_req)

    mock_extractor = MemoryExtractor()
    mock_extractor.extract = AsyncMock(
        return_value={
            "entities": [{"ref": "e1", "name": "Port 8080", "kind": "tool", "aliases": []}],
            "memories": [
                {
                    "ref": "m1",
                    "type": "fact",
                    "epistemic": "fact",
                    "asserted_by": "dev",
                    "statement": "The server runs on port 8080 by default.",
                    "subject": None,
                    "predicate": "server_port",
                    "object": "e1",
                    "object_value": "8080",
                    "evidence": ["The server runs on port 8080 by default"],
                    "confidence": 0.95,
                    "importance": 0.7,
                    "lane": "code",
                    "sensitivity": "normal",
                    "about_speaker": False,
                }
            ],
        }
    )

    pipeline = SavePipeline(pool, extractor=mock_extractor)
    await pipeline.run(cap_res.event_id)

    # Perform a retrieval query via /v1/context
    ctx_resp = await client.post(
        "/v1/context",
        json={"query": "Which port does the server run on?", "context": "ctx_trace_test", "budget_tokens": 1024, "threshold": 0.05},
    )
    assert ctx_resp.status_code == 200

    # Query GET /v1/viewer/traces
    traces_resp = await client.get("/v1/viewer/traces?limit=10")
    assert traces_resp.status_code == 200
    traces = traces_resp.json()
    assert len(traces) >= 1
    latest = traces[0]
    assert "query" in latest
    assert "latency_ms" in latest
    assert "channels" in latest
    assert "returned_ids" in latest
