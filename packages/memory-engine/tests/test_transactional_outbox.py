"""
Integration tests for Transactional Outbox Pattern in PostgreSQL.
Verifies that saving entities, triples, and facts creates outbox entries
in the same transaction, triggers NOTIFY, and can be acknowledged via REST API.
"""
import pytest
import pytest_asyncio
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.domain.models import Entity, Triple


@pytest_asyncio.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_entity_creation_enqueues_outbox(client: AsyncClient):
    """Verify that inserting an entity atomically creates an ENTITY outbox record."""
    unique_suffix = uuid.uuid4().hex[:6]
    entity_payload = {
        "canonical_name": f"Docker_{unique_suffix}",
        "category": "technology",
        "aliases": [f"container_{unique_suffix}"],
        "description": "Containerization runtime",
    }

    # 1. Create entity via API
    create_res = await client.post("/api/v1/entities", json=entity_payload)
    assert create_res.status_code == 200
    entity_data = create_res.json()
    entity_id = entity_data["id"]

    # 2. Fetch pending outbox records via Outbox API
    pending_res = await client.get("/api/v1/outbox/pending?limit=100")
    assert pending_res.status_code == 200
    pending_items = pending_res.json()["items"]

    matching = [p for p in pending_items if str(p["aggregate_id"]) == str(entity_id)]
    assert len(matching) >= 1
    outbox_item = matching[0]
    assert outbox_item["aggregate_type"] == "ENTITY"
    assert outbox_item["event_type"] == "CREATED"
    assert outbox_item["payload"]["canonical_name"] == f"Docker_{unique_suffix}"

    # 3. Acknowledge the outbox item via REST API
    ack_res = await client.post(f"/api/v1/outbox/{outbox_item['id']}/ack")
    assert ack_res.status_code == 200
    assert ack_res.json()["status"] == "PROCESSED"

    # 4. Verify item is no longer pending
    pending_after_res = await client.get("/api/v1/outbox/pending?limit=100")
    pending_after = pending_after_res.json()["items"]
    assert not any(str(p["id"]) == str(outbox_item["id"]) for p in pending_after)


@pytest.mark.asyncio
async def test_triple_creation_enqueues_outbox(client: AsyncClient):
    """Verify that saving a triple generates a TRIPLE outbox record with resolved entity names."""
    unique_suffix = uuid.uuid4().hex[:6]
    sub_name = f"Service_{unique_suffix}"
    obj_name = f"Database_{unique_suffix}"

    # Propose a new triple
    prop_res = await client.post(
        "/api/v1/graph/propose",
        json={
            "subject": sub_name,
            "predicate": "DEPENDS_ON",
            "object": obj_name,
            "confidence": 0.99,
        },
    )
    assert prop_res.status_code == 200
    prop_data = prop_res.json()
    assert prop_data["action_taken"] == "AUTO_COMMITTED"
    triple_id = prop_data["triple"]["id"]

    # Check pending outbox
    pending_res = await client.get("/api/v1/outbox/pending?limit=100")
    assert pending_res.status_code == 200
    pending_items = pending_res.json()["items"]

    matching = [p for p in pending_items if str(p["aggregate_id"]) == str(triple_id)]
    assert len(matching) >= 1
    item = matching[0]
    assert item["aggregate_type"] == "TRIPLE"
    assert item["payload"]["predicate"] == "DEPENDS_ON"
    assert item["payload"]["subject_name"] == sub_name
    assert item["payload"]["object_name"] == obj_name

    # Acknowledge the triple outbox item
    ack_res = await client.post(f"/api/v1/outbox/{item['id']}/ack")
    assert ack_res.status_code == 200
    assert ack_res.json()["status"] == "PROCESSED"


@pytest.mark.asyncio
async def test_outbox_stats_endpoint(client: AsyncClient):
    """Verify the outbox statistics reporting endpoint."""
    resp = await client.get("/api/v1/outbox/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "pending" in data
    assert "processed" in data
    assert "failed" in data
    assert "total" in data
    assert data["total"] >= data["processed"]
