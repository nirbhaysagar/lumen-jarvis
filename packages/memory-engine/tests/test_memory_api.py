"""
Integration Tests for Lumen Memory Engine API.
Verifies Hexagonal Architecture, Postgres+pgvector storage, Idempotency, and Trace Provenance.
"""
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest_asyncio.fixture
async def client():
    """Test client fixture that triggers FastAPI lifespan (connecting to DB pool)."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_health_and_correlation_id(client: AsyncClient):
    """Verify health endpoint and X-Trace-ID propagation."""
    response = await client.get("/health", headers={"X-Trace-ID": "tr_custom_test_123"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "memory-engine"
    assert response.headers["X-Trace-ID"] == "tr_custom_test_123"


@pytest.mark.asyncio
async def test_event_idempotency_and_trace_pipeline(client: AsyncClient):
    """
    End-to-end provenance test:
    1. Ingest raw event with idempotency key.
    2. Ingest duplicate event -> asserts idempotent_hit is True and returns same ID.
    3. Create canonical fact linking to source event.
    4. Search memories via vector similarity -> finds fact.
    5. Trace fact -> verifies exact link back to raw event payload.
    """
    # Step 1: Ingest Raw Event (L0)
    test_key = f"idem_test_{uuid.uuid4()}"
    event_payload = {
        "source": "user_cli",
        "conversation_id": "conv_init_test",
        "idempotency_key": test_key,
        "payload": {
            "message": "We decided to use PostgreSQL for Lumen and reject MongoDB.",
            "speaker": "user",
        },
    }
    resp1 = await client.post("/api/v1/events", json=event_payload)
    assert resp1.status_code == 200
    data1 = resp1.json()
    event_id = data1["id"]
    assert data1["idempotent_hit"] is False

    # Step 2: Test Idempotency with exact same key
    resp2 = await client.post("/api/v1/events", json=event_payload)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["id"] == event_id
    assert data2["idempotent_hit"] is True

    # Step 3: Create Derived Canonical Fact (L1) with Provenance link
    test_entity = f"project_lumen_{uuid.uuid4().hex[:6]}"
    fact_payload = {
        "entity": test_entity,
        "attribute": "chosen_database",
        "value": "PostgreSQL with pgvector",
        "memory_type": "decision",
        "epistemic_type": "decision",
        "confidence": 1.0,
        "source_event_ids": [event_id],
    }
    fact_resp = await client.post("/api/v1/facts", json=fact_payload)
    assert fact_resp.status_code == 200
    fact_data = fact_resp.json()
    fact_id = fact_data["id"]

    # Step 4: Semantic Vector Search
    search_payload = {
        "query": "Which database did we select for project Lumen?",
        "limit": 5,
        "threshold": 0.05,
    }
    search_resp = await client.post("/api/v1/memories/search", json=search_payload)
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    assert search_data["total"] >= 1
    found_fact_ids = [r["id"] for r in search_data["results"]]
    assert fact_id in found_fact_ids

    # Step 5: Verify Provenance Trace
    trace_resp = await client.get(f"/api/v1/trace/{fact_id}")
    assert trace_resp.status_code == 200
    trace_data = trace_resp.json()
    assert trace_data["fact"]["id"] == fact_id
    assert len(trace_data["source_events"]) == 1
    assert trace_data["source_events"][0]["id"] == event_id
    assert "reject MongoDB" in trace_data["source_events"][0]["payload"]["message"]


@pytest.mark.asyncio
async def test_bi_temporal_superseding_and_time_travel(client: AsyncClient):
    """
    Phase 3 Bi-Temporal Test:
    1. Records historical Fact V1 ("VS Code").
    2. Records new Fact V2 ("Cursor") for the same entity and attribute.
    3. Verifies Fact V1 was automatically superseded (valid_to populated) without being deleted.
    4. Verifies Trace API exposes full lineage (V1 -> V2).
    5. Performs Time-Travel Search reconstructing past state vs present state.
    """
    entity = f"pref_editor_{uuid.uuid4().hex[:6]}"
    attribute = "editor"

    # Step 1: Create V1 Event and Fact
    e1_resp = await client.post("/api/v1/events", json={
        "source": "cli",
        "conversation_id": "conv_bt_1",
        "payload": {"text": "I exclusively use VS Code for coding."},
    })
    e1_id = e1_resp.json()["id"]

    v1_resp = await client.post("/api/v1/facts", json={
        "entity": entity,
        "attribute": attribute,
        "value": "VS Code",
        "memory_type": "preference",
        "epistemic_type": "decision",
        "confidence": 1.0,
        "source_event_ids": [e1_id],
    })
    assert v1_resp.status_code == 200
    v1_data = v1_resp.json()
    v1_id = v1_data["id"]
    assert v1_data["valid_to"] is None

    # Step 2: Create V2 Event and Fact (Supersedes V1)
    e2_resp = await client.post("/api/v1/events", json={
        "source": "cli",
        "conversation_id": "conv_bt_1",
        "payload": {"text": "We have officially migrated from VS Code to Cursor."},
    })
    e2_id = e2_resp.json()["id"]

    v2_resp = await client.post("/api/v1/facts", json={
        "entity": entity,
        "attribute": attribute,
        "value": "Cursor",
        "memory_type": "preference",
        "epistemic_type": "decision",
        "confidence": 1.0,
        "source_event_ids": [e2_id],
    })
    assert v2_resp.status_code == 200
    v2_data = v2_resp.json()
    v2_id = v2_data["id"]
    assert v2_data["valid_to"] is None

    # Step 3: Verify Trace on V2 contains V1 in history lineage and V1 has valid_to set!
    trace_resp = await client.get(f"/api/v1/trace/{v2_id}")
    assert trace_resp.status_code == 200
    trace_data = trace_resp.json()
    assert trace_data["fact"]["id"] == v2_id
    assert len(trace_data["source_events"]) == 1
    assert trace_data["source_events"][0]["id"] == e2_id

    lineage = trace_data["history_lineage"]
    assert len(lineage) == 2
    # Lineage ordered by valid_from ASC: first is V1, second is V2
    assert lineage[0]["id"] == v1_id
    assert lineage[0]["value"] == "VS Code"
    assert lineage[0]["valid_to"] is not None  # Automatically superseded!
    assert lineage[1]["id"] == v2_id
    assert lineage[1]["value"] == "Cursor"
    assert lineage[1]["valid_to"] is None  # Currently active!

    # Step 4: Time-Travel Vector Search
    # Query at V1's historical valid_from -> must find VS Code
    tt_past_resp = await client.post("/api/v1/memories/time-travel", json={
        "query": "Which editor do I use?",
        "as_of": v1_data["valid_from"],
        "threshold": 0.01,
    })
    assert tt_past_resp.status_code == 200
    tt_past = tt_past_resp.json()
    past_ids = [r["id"] for r in tt_past["results"]]
    assert v1_id in past_ids
    assert v2_id not in past_ids

    # Query currently (active state) -> must find Cursor, NOT VS Code
    search_curr_resp = await client.post("/api/v1/memories/search", json={
        "query": "Which editor do I use?",
        "threshold": 0.01,
    })
    assert search_curr_resp.status_code == 200
    search_curr = search_curr_resp.json()
    curr_ids = [r["id"] for r in search_curr["results"]]
    assert v2_id in curr_ids
    assert v1_id not in curr_ids
