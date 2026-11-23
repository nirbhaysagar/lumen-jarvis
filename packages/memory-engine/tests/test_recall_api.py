"""
Integration Tests for 5-Channel Recall API & Extraction API (Milestone M5).
Verifies:
  1. POST /v1/capture captures raw interaction.
  2. POST /v1/extract performs structured extraction, character-span verification, and embedding.
  3. POST /v1/recall executes 5-channel hybrid recall (vector, lexical, entity, graph, temporal)
     and returns citation tags [M1], [M2].
"""
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from app.core.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_end_to_end_capture_extract_and_recall():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}

            # 1. Capture raw event
            raw_text = "Nirbhay decided to use PostgreSQL 17 as the primary database for Lumen."
            cap_resp = await client.post(
                "/v1/capture",
                headers=headers,
                json={
                    "content": raw_text,
                    "source": {"kind": "chat", "title": "architecture discussion", "author": "nirbhay"},
                    "idempotency_key": f"test_e2e_{uuid.uuid4()}",
                    "context": "default",
                },
            )
            assert cap_resp.status_code == 201
            cap_data = cap_resp.json()
            event_id = cap_data["event_id"]
            assert event_id is not None

            # 2. Extract and apply structured memories
            ext_resp = await client.post(
                "/v1/extract",
                headers=headers,
                json={
                    "event_id": event_id,
                    "content": raw_text,
                    "speaker": "nirbhay",
                },
            )
            assert ext_resp.status_code == 200
            ext_data = ext_resp.json()
            assert ext_data["memories_created"] >= 1
            assert len(ext_data["memories"]) >= 1

            # 3. Recall using natural language query
            rec_resp = await client.post(
                "/v1/recall",
                headers=headers,
                json={
                    "query": "What database does Lumen use?",
                    "context": "default",
                    "limit": 5,
                },
            )
            assert rec_resp.status_code == 200
            rec_data = rec_resp.json()
            assert rec_data["count"] >= 1
            top_mem = rec_data["results"][0]
            assert top_mem["citation_tag"] == "[M1]"
            assert "PostgreSQL" in top_mem["statement"] or "postgres" in top_mem["statement"].lower()
            assert len(top_mem["citations"]) >= 1
            cit = top_mem["citations"][0]
            assert cit["verified"] is True
            assert "PostgreSQL" in cit["quote"] or "postgres" in cit["quote"].lower()
