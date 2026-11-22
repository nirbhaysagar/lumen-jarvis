"""
Integration Tests for Knowledge Graph Entities, Triples, and Review Queue API.
Verifies Two-Tier Entity Resolution, Canonical Alias Merging, Tiered Contradiction Gating,
and Recursive Multi-Hop Graph Traversal.
"""
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app




@pytest.mark.asyncio
async def test_entity_creation_and_alias_resolution(client: AsyncClient):
    """Test creating an entity with aliases and retrieving it via alias."""
    # 1. Create canonical entity "PostgreSQL" with alias "psql"
    resp = await client.post(
        "/api/v1/entities",
        json={
            "canonical_name": "PostgreSQL",
            "category": "technology",
            "aliases": ["psql", "postgres"],
            "description": "Advanced open-source relational database",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["canonical_name"] == "PostgreSQL"
    assert "psql" in data["aliases"]
    entity_id = data["id"]

    # 2. Look up entity using the alias "psql"
    lookup_resp = await client.get("/api/v1/entities/psql")
    assert lookup_resp.status_code == 200
    lookup_data = lookup_resp.json()
    assert lookup_data["id"] == entity_id
    assert lookup_data["canonical_name"] == "PostgreSQL"


@pytest.mark.asyncio
async def test_tier_a_auto_commit_triple(client: AsyncClient):
    """Test non-conflicting high-confidence triple auto-commits directly to canonical memory."""
    req_payload = {
        "subject": "Puneet",
        "predicate": "FOUNDED",
        "object": "Cerebro Dynamics",
        "confidence": 1.0,
        "subject_category": "person",
        "object_category": "organization",
    }
    resp = await client.post("/api/v1/graph/propose", json=req_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["action_taken"] == "AUTO_COMMITTED"
    assert data["triple"] is not None
    assert data["proposal"]["status"] == "AUTO_COMMITTED"
    assert data["proposal"]["tier"] == "TIER_A"
    assert data["proposal"]["resolved_predicate"] == "FOUNDED"


@pytest.mark.asyncio
async def test_tier_b_contradiction_gating_and_approval(client: AsyncClient):
    """
    Test contradiction detection and human review queue approval:
    1. User PREFERS Neovim (auto-commits).
    2. User PREFERS VS Code (contradicts Neovim -> staged in Review Queue as PENDING).
    3. Human approves proposal -> retires Neovim and activates VS Code.
    """
    unique_user = f"Dev_{uuid.uuid4().hex[:6]}"

    # Step 1: Initial preference -> Auto-commits
    step1_resp = await client.post(
        "/api/v1/graph/propose",
        json={
            "subject": unique_user,
            "predicate": "PREFERS",
            "object": "Neovim",
            "confidence": 1.0,
        },
    )
    assert step1_resp.status_code == 200
    assert step1_resp.json()["action_taken"] == "AUTO_COMMITTED"
    neovim_triple_id = step1_resp.json()["triple"]["id"]

    # Step 2: Conflicting preference -> Staged in Review Queue as PENDING
    step2_resp = await client.post(
        "/api/v1/graph/propose",
        json={
            "subject": unique_user,
            "predicate": "PREFERS",
            "object": "VS Code",
            "confidence": 1.0,
        },
    )
    assert step2_resp.status_code == 200
    step2_data = step2_resp.json()
    assert step2_data["action_taken"] == "STAGED_FOR_REVIEW"
    assert step2_data["triple"] is None
    proposal = step2_data["proposal"]
    assert proposal["tier"] == "TIER_B"
    assert proposal["status"] == "PENDING"
    assert "Contradiction with active belief" in proposal["conflict_reason"]
    proposal_id = proposal["id"]

    # Step 3: Verify proposal appears in Review Queue
    queue_resp = await client.get("/api/v1/graph/review-queue?status=PENDING")
    assert queue_resp.status_code == 200
    pending_list = queue_resp.json()
    assert any(p["id"] == proposal_id for p in pending_list)

    # Step 4: Human Authorizes Proposal (/approve)
    resolve_resp = await client.post(
        f"/api/v1/graph/review-queue/{proposal_id}/resolve",
        json={"decision": "APPROVED"},
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_recursive_graph_traversal(client: AsyncClient):
    """Test multi-hop graph traversal using recursive SQL."""
    # Seed a 3-hop graph chain:
    # Founder -> Company -> Project -> Database
    root_founder = f"Founder_{uuid.uuid4().hex[:6]}"
    comp = f"Venture_{uuid.uuid4().hex[:6]}"
    proj = f"OS_{uuid.uuid4().hex[:6]}"
    db = f"Engine_{uuid.uuid4().hex[:6]}"

    await client.post("/api/v1/graph/propose", json={"subject": root_founder, "predicate": "FOUNDED", "object": comp})
    await client.post("/api/v1/graph/propose", json={"subject": comp, "predicate": "DEVELOPS", "object": proj})
    await client.post("/api/v1/graph/propose", json={"subject": proj, "predicate": "USES", "object": db})

    # Traverse starting from founder with depth 3
    traverse_resp = await client.get(f"/api/v1/graph/traverse/{root_founder}?max_depth=3")
    assert traverse_resp.status_code == 200
    data = traverse_resp.json()
    assert data["total_edges"] >= 3

    node_names = {e["object_name"] for e in data["edges"]} | {e["subject_name"] for e in data["edges"]}
    assert root_founder in node_names
    assert comp in node_names
    assert proj in node_names
    assert db in node_names
