"""
Integration Tests for Unified Hybrid Retrieval with Reciprocal Rank Fusion (RRF).
Verifies lexical BM25/tsvector, dense vector pgvector, and graph associative retrieval channels.
"""
import pytest
import pytest_asyncio
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.domain.models import Entity, Triple, CanonicalFact, MemoryType, EpistemicType


@pytest_asyncio.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_lexical_fact_search(client: AsyncClient):
    """Test full-text tsvector/trigram search on facts table."""
    storage = app.state.storage
    embedding_adapter = app.state.embedding
    unique_term = f"KubernetesEngine_{uuid.uuid4().hex[:6]}"

    fact = CanonicalFact(
        entity="infrastructure",
        attribute="orchestrator",
        value=f"We deployed on {unique_term} in production",
        memory_type=MemoryType.DECISION,
        epistemic_type=EpistemicType.DECISION,
    )
    vec = await embedding_adapter.generate_embedding(str(fact.value))
    await storage.save_fact(fact, vec)

    # Search via lexical facts query
    lex_facts = await storage.search_lexical_facts(query=unique_term, limit=5)
    assert len(lex_facts) >= 1
    assert any(unique_term in str(f.value) for f in lex_facts)


@pytest.mark.asyncio
async def test_graph_assisted_fact_search(client: AsyncClient):
    """Test retrieving facts through knowledge graph entity traversal."""
    storage = app.state.storage
    embedding_adapter = app.state.embedding
    unique = uuid.uuid4().hex[:6]

    # Create Subject Puneet -> DEVELOPS -> Lumen
    e_sub = Entity(canonical_name=f"Developer_{unique}", category="person")
    e_obj = Entity(canonical_name=f"Project_{unique}", category="project")
    sub_id = await storage.save_entity(e_sub)
    obj_id = await storage.save_entity(e_obj)

    triple = Triple(subject_id=sub_id, predicate="DEVELOPS", object_id=obj_id)
    await storage.save_triple(triple)

    # Create a fact on Project_{unique}
    fact = CanonicalFact(
        entity=f"Project_{unique}",
        attribute="architecture",
        value="Hexagonal microservice architecture with pgvector",
        memory_type=MemoryType.FACT,
    )
    vec = await embedding_adapter.generate_embedding(str(fact.value))
    await storage.save_fact(fact, vec)

    # Query using Subject's name ("Developer_{unique}")
    # Graph traversal links Developer_{unique} -> Project_{unique} -> fact!
    graph_facts = await storage.search_graph_facts(query=f"Developer_{unique}", limit=5)
    assert len(graph_facts) >= 1
    assert any(f.entity == f"Project_{unique}" for f in graph_facts)


@pytest.mark.asyncio
async def test_unified_rrf_hybrid_search(client: AsyncClient):
    """Test end-to-end RRF hybrid search endpoint merging channels with mathematical rank fusion."""
    storage = app.state.storage
    embedding_adapter = app.state.embedding
    unique = uuid.uuid4().hex[:6]
    unique_keyword = f"PostgresVectorIndex_{unique}"

    fact = CanonicalFact(
        entity="database_config",
        attribute="vector_index",
        value=f"Using HNSW index with {unique_keyword} for fast similarity search",
        memory_type=MemoryType.DECISION,
    )
    vec = await embedding_adapter.generate_embedding(str(fact.value))
    fact_id = await storage.save_fact(fact, vec)

    # Call REST /search/hybrid endpoint
    resp = await client.post(
        "/api/v1/search/hybrid",
        json={
            "query": unique_keyword,
            "limit": 5,
            "mode": "hybrid",
            "k_rrf": 60,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_results"] >= 1

    top_result = data["results"][0]
    assert top_result["id"] == str(fact_id)
    assert top_result["rrf_score"] > 0.0
    # Channel ranks should track where it was found
    assert "channel_ranks" in top_result
    assert isinstance(top_result["channel_ranks"], dict)
    assert ("lexical" in top_result["channel_ranks"]) or ("dense" in top_result["channel_ranks"])
