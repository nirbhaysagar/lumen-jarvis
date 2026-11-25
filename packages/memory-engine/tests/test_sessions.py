"""
Tests for Session Synopses & Continuity (Milestone L2).
Verifies:
1. POST /v1/sessions/{id}/close generates <=150 word synopsis stored in abstractions.
2. Provenance links written to abstraction_sources.
3. GET /v1/sessions/prime returns top synopses labeled epistemic='inference'.
"""
import json
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from app.core.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_session_close_and_prime():
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}
    ctx_slug = f"sess_ctx_{uuid.uuid4().hex[:6]}"
    session_id = f"sess_{uuid.uuid4().hex[:8]}"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            pool = app.state.storage.pool

            async with pool.acquire() as conn:
                ctx_id = await conn.fetchval(
                    "INSERT INTO contexts (id, slug, name) VALUES ($1, $2, $3) RETURNING id",
                    uuid.uuid4(), ctx_slug, "Session Context"
                )
                ev_id = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO events (id, context_id, kind, actor, payload) VALUES ($1, $2, 'CAPTURE', 'user', $3)",
                    ev_id, ctx_id, json.dumps({"text": "Decided to use FastMCP for Lumen MCP server.", "session_id": session_id})
                )
                m_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement,
                                          type, epistemic, asserted_by, confidence)
                    VALUES ($1, $1, $2, $3, 1, 'active', 'Lumen uses FastMCP for MCP server', 'decision', 'decision', 'user', 1.0)
                    """,
                    m_id, ctx_id, ev_id
                )

            # 1. Close session
            close_res = await client.post(
                f"/v1/sessions/{session_id}/close",
                json={
                    "project": "lumen",
                    "branch": "feat/memory-engine",
                    "context": ctx_slug,
                    "summary_override": "Decided to use FastMCP for the Lumen MCP server and completed Phase 2.5.",
                },
                headers=headers,
            )
            assert close_res.status_code == 200
            c_data = close_res.json()
            assert c_data["status"] == "closed"
            assert c_data["epistemic"] == "inference"
            assert c_data["proof_count"] >= 1
            assert len(c_data["synopsis"].split()) <= 150

            # 2. Prime new session
            prime_res = await client.get(
                f"/v1/sessions/prime?project=lumen&branch=feat/memory-engine&context={ctx_slug}&limit=3",
                headers=headers,
            )
            assert prime_res.status_code == 200
            p_data = prime_res.json()
            assert p_data["count"] >= 1
            top = p_data["synopses"][0]
            assert top["epistemic"] == "inference"
            assert "FastMCP" in top["synopsis"]
