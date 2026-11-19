"""
Integration Tests for Knowledge Graph Overnight Consolidation.
Verifies contradiction patrols, functional predicate collision detection,
semantic alias clustering, orphan detection, and review queue staging.
"""
import pytest
import pytest_asyncio
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.domain.models import Entity, Triple, ProposalStatus, ProposalTier


@pytest_asyncio.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_consolidation_opposing_predicates(client: AsyncClient):
    """Test contradiction patrol detects opposing predicates (PREFERS vs DISLIKES)."""
    storage = app.state.storage
    unique = uuid.uuid4().hex[:6]

    # Create Subject and Object
    sub = Entity(canonical_name=f"User_{unique}", category="person")
    obj = Entity(canonical_name=f"Tool_{unique}", category="technology")
    sub_id = await storage.save_entity(sub)
    obj_id = await storage.save_entity(obj)

    # Insert opposing triples directly into database: PREFERS and DISLIKES
    t1 = Triple(subject_id=sub_id, predicate="PREFERS", object_id=obj_id, confidence=1.0)
    t2 = Triple(subject_id=sub_id, predicate="DISLIKES", object_id=obj_id, confidence=1.0)
    await storage.save_triple(t1)
    await storage.save_triple(t2)

    # Trigger consolidation
    resp = await client.post("/api/v1/graph/consolidate")
    assert resp.status_code == 200
    report = resp.json()

    assert report["conflicts_detected"] >= 1

    # Verify a CONTRADICTION_UPDATE proposal was staged in review queue
    q_resp = await client.get("/api/v1/graph/review-queue?status=PENDING")
    assert q_resp.status_code == 200
    proposals = q_resp.json()

    matching = [
        p for p in proposals
        if p["proposal_type"] == "CONTRADICTION_UPDATE"
        and f"User_{unique}" in p["subject_raw"]
        and f"Tool_{unique}" in p["object_raw"]
    ]
    assert len(matching) >= 1
    assert "Opposing predicate collision" in matching[0]["conflict_reason"]
    assert matching[0]["tier"] == "TIER_B"


@pytest.mark.asyncio
async def test_consolidation_functional_predicate_collision(client: AsyncClient):
    """Test contradiction patrol detects multi-value collisions on functional predicates."""
    storage = app.state.storage
    unique = uuid.uuid4().hex[:6]

    # Subject with multiple targets for functional predicate FOUNDED
    sub = Entity(canonical_name=f"Founder_{unique}", category="person")
    co1 = Entity(canonical_name=f"CompanyA_{unique}", category="organization")
    co2 = Entity(canonical_name=f"CompanyB_{unique}", category="organization")
    sub_id = await storage.save_entity(sub)
    co1_id = await storage.save_entity(co1)
    co2_id = await storage.save_entity(co2)

    t1 = Triple(subject_id=sub_id, predicate="FOUNDED", object_id=co1_id, confidence=1.0)
    t2 = Triple(subject_id=sub_id, predicate="FOUNDED", object_id=co2_id, confidence=1.0)
    await storage.save_triple(t1)
    await storage.save_triple(t2)

    resp = await client.post("/api/v1/graph/consolidate")
    assert resp.status_code == 200
    report = resp.json()
    assert report["conflicts_detected"] >= 1

    # Check that collision is recorded in details
    functional_cols = report["details"]["functional_collisions"]
    matching_cols = [c for c in functional_cols if c["subject_name"] == f"Founder_{unique}"]
    assert len(matching_cols) >= 1
    assert matching_cols[0]["predicate"] == "FOUNDED"


@pytest.mark.asyncio
async def test_list_orphan_entities(client: AsyncClient):
    """Test listing orphan nodes (degree <= 1)."""
    storage = app.state.storage
    unique = uuid.uuid4().hex[:6]

    # Create an entity with zero edges
    orphan = Entity(canonical_name=f"IsolatedEntity_{unique}", category="concept")
    orphan_id = await storage.save_entity(orphan)

    resp = await client.get("/api/v1/graph/orphans?max_degree=1")
    assert resp.status_code == 200
    orphans = resp.json()

    assert any(o["id"] == str(orphan_id) for o in orphans)
