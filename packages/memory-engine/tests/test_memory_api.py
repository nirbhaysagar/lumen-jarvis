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
    fact_payload = {
        "entity": "project_lumen",
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
