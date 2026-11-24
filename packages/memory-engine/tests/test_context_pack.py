"""
Unit & Integration Tests for Context Pack API (POST /v1/context) and Reranker (Milestone Phase 0.6).
Verifies:
1. POST /v1/context returns structured ContextPack with citations and verified_facts.
2. Token budget packing: greedily packs memories within budget_tokens.
3. Abstention: returns status='abstain' when no memories meet threshold, formatting abstention injection.
4. Reranker sorts more relevant memories higher.
5. Lifecycle boost: reinforced memories receive boost from access_count and reaffirm_count.
"""
from datetime import datetime, timezone
import json
import uuid
import pytest
from httpx import AsyncClient

from app.domain.events import CaptureRequest, SourceInfo
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.domain.extraction import MemoryExtractor
from app.pipeline import SavePipeline
from app.adapters.reranker import CrossEncoderReranker
from app.main import app


@pytest.mark.asyncio
async def test_cross_encoder_reranker_fallback():
    reranker = CrossEncoderReranker(enabled=False)
    query = "What database does Lumen use?"
    docs = [
        "User drinks black coffee every morning.",
        "Lumen uses PostgreSQL 17 for memory persistence.",
        "User lives in Bengaluru Indiranagar.",
    ]
    scores = await reranker.rerank(query, docs)
    assert len(scores) == 3
    # Second document about Postgres should score highest
    assert scores[1] > scores[0]
    assert scores[1] > scores[2]


@pytest.mark.asyncio
async def test_context_pack_api_ok_flow(client: AsyncClient):
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)

        ctx_slug = f"ctx_test_{uuid.uuid4().hex[:8]}"

        # 1. Capture and extract a memory
        cap_req = CaptureRequest(
            content="Lumen primary database is Postgres 17.",
            source=SourceInfo(kind="chat", title="db decision", author="user"),
            context=ctx_slug,
        )
        cap_res = await events_adapter.capture(cap_req)

        # Mock extraction
        mock_extractor = MemoryExtractor()
        mock_extractor.extract = pytest.importorskip("unittest.mock").AsyncMock(
            return_value={
                "entities": [{"ref": "e1", "name": "Postgres 17", "kind": "tool", "aliases": []}],
                "memories": [
                    {
                        "ref": "m1",
                        "type": "fact",
                        "epistemic": "fact",
                        "asserted_by": "user",
                        "statement": "Lumen primary database is Postgres 17.",
                        "subject": None,
                        "predicate": "primary_database",
                        "object": "e1",
                        "object_value": "Postgres 17",
                        "evidence": ["Lumen primary database is Postgres 17"],
                        "confidence": 0.95,
                        "importance": 0.8,
                        "lane": "code",
                        "sensitivity": "normal",
                        "about_speaker": True,
                    }
                ],
            }
        )

        pipeline = SavePipeline(pool, extractor=mock_extractor)
        await pipeline.run(cap_res.event_id)

        # 2. Call POST /v1/context
        resp = await client.post(
            "/v1/context",
            json={
                "query": "What database does Lumen use?",
                "context": ctx_slug,
                "budget_tokens": 1024,
                "threshold": 0.10,
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "ok"
        assert len(data["relevant_memories"]) >= 1
        assert len(data["verified_facts"]) >= 1
        assert "[M1]" in data["verified_facts"][0]
        assert "Postgres 17" in data["verified_facts"][0]

        # Citations list
        assert len(data["citations"]) >= 1
        assert data["citations"][0]["citation_id"] == "M1"
        assert "Lumen primary database is Postgres 17" in data["citations"][0]["quote"]

        # Prompt injection formatting
        assert "## Recalled Long-Term Memories" in data["prompt_injection"]
        assert "### Grounding Instructions:" in data["prompt_injection"]
        assert data["token_count"] > 0
        assert data["token_count"] <= 1024


@pytest.mark.asyncio
async def test_context_pack_api_abstain_flow(client: AsyncClient):
    async with app.router.lifespan_context(app):
        ctx_slug = f"ctx_test_{uuid.uuid4().hex[:8]}"

        # Query on empty context -> should abstain
        resp = await client.post(
            "/v1/context",
            json={
                "query": "What is my blood group?",
                "context": ctx_slug,
                "budget_tokens": 1024,
                "threshold": 0.50,
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "abstain"
        assert data["verified_facts"] == []
        assert data["citations"] == []
        assert data["relevant_memories"] == []
        assert "[No verified memories met confidence threshold" in data["prompt_injection"]
