"""
Tests for Extraction Reliability & Dead-Letter Staging (Milestone M0.3).
Verifies:
  1. Truncated output (done_reason='length') -> HTTP 422 + review_queue(failed_extraction).
  2. Invalid JSON twice -> HTTP 422 + extraction_attempts record.
  3. Schema violation that succeeds on retry -> HTTP 200.
  4. Valid empty output -> HTTP 200 with 0 memories and no review row.
  5. POST /v1/extract/{event_id}/retry resolves review_queue entry.
"""
import json
import uuid
import pytest
import httpx
from httpx import AsyncClient, ASGITransport

from app.core.config import settings
from app.adapters.llm.ollama_client import OllamaClient
from app.domain.extraction import MemoryExtractor
from app.main import app


@pytest.mark.asyncio
async def test_truncated_output_stages_dead_letter_and_returns_422(monkeypatch):
    """Verify that truncation triggers ExtractionError, stages to review_queue, and returns 422."""
    async def mock_handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "message": {"content": '{"memories": [{"statement": "Incomplet'},
                "done_reason": "length",
            },
        )

    mock_client = OllamaClient(
        base_url="http://mock-ollama",
        transport=httpx.MockTransport(mock_handler),
    )
    monkeypatch.setattr(
        "app.api.v1.extract.MemoryExtractor",
        lambda *args, **kwargs: MemoryExtractor(client=mock_client),
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
            # 1. Capture event first
            cap_resp = await c.post(
                "/v1/capture",
                json={
                    "content": "A very long text that gets truncated.",
                    "source": {"kind": "chat", "title": "truncation test"},
                    "idempotency_key": f"test_trunc_{uuid.uuid4()}",
                },
            )
            assert cap_resp.status_code in (200, 201)
            ev_id = cap_resp.json()["event_id"]

            # 2. Extract should fail with 422
            ext_resp = await c.post("/v1/extract", json={"event_id": ev_id})
            assert ext_resp.status_code == 422
            data = ext_resp.json()
            assert data["error"] == "extraction_failed"
            assert data["kind"] == "truncated"
            assert "review_id" in data

            # 3. Verify in database review_queue
            pool = app.state.db_pool
            async with pool.acquire() as conn:
                rq_row = await conn.fetchrow(
                    "SELECT * FROM review_queue WHERE kind = 'failed_extraction' AND proposal->>'event_id' = $1",
                    ev_id,
                )
                assert rq_row is not None
                assert rq_row["status"] == "open"
                assert rq_row["reason"] == "truncated"

                attempt_row = await conn.fetchrow(
                    "SELECT * FROM extraction_attempts WHERE event_id = $1",
                    uuid.UUID(ev_id),
                )
                assert attempt_row is not None
                assert attempt_row["ok"] is False
                assert attempt_row["error_kind"] == "truncated"


@pytest.mark.asyncio
async def test_invalid_json_twice_returns_422(monkeypatch):
    """Verify invalid JSON twice results in 422 and dead-letter staging."""
    async def mock_handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "message": {"content": "This is definitely not JSON output"},
                "done_reason": "stop",
            },
        )

    mock_client = OllamaClient(
        base_url="http://mock-ollama",
        transport=httpx.MockTransport(mock_handler),
    )
    monkeypatch.setattr(
        "app.api.v1.extract.MemoryExtractor",
        lambda *args, **kwargs: MemoryExtractor(client=mock_client),
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
            cap_resp = await c.post(
                "/v1/capture",
                json={
                    "content": "Testing invalid JSON formatting.",
                    "source": {"kind": "chat", "title": "json test"},
                    "idempotency_key": f"test_json_{uuid.uuid4()}",
                },
            )
            ev_id = cap_resp.json()["event_id"]

            ext_resp = await c.post("/v1/extract", json={"event_id": ev_id})
            assert ext_resp.status_code == 422
            data = ext_resp.json()
            assert data["error"] == "extraction_failed"
            assert data["kind"] == "json_decode"


@pytest.mark.asyncio
async def test_schema_violation_fixed_on_retry(monkeypatch):
    """Verify that a schema violation on first try that is repaired on retry succeeds with 200."""
    call_count = 0

    async def mock_handler(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Bad schema: invalid enum for memory type
            bad_json = {
                "entities": [],
                "memories": [{"statement": "User uses Python", "type": "INVALID_TYPE_NOT_IN_ENUM"}],
            }
            return httpx.Response(
                200,
                json={
                    "message": {"content": json.dumps(bad_json)},
                    "done_reason": "stop",
                },
            )
        # Good schema on retry
        valid_json = {
            "entities": [{"ref": "e1", "name": "Python", "kind": "tool"}],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "User uses Python",
                "subject": "e1",
                "predicate": "uses",
                "object": None,
                "object_value": "Python",
                "cardinality": "many",
                "polarity": True,
                "evidence": ["uses Python"],
                "confidence": 0.95,
                "importance": 0.8,
            }],
        }
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(valid_json)}, "done_reason": "stop"},
        )

    mock_client = OllamaClient(
        base_url="http://mock-ollama",
        transport=httpx.MockTransport(mock_handler),
    )
    monkeypatch.setattr(
        "app.api.v1.extract.MemoryExtractor",
        lambda *args, **kwargs: MemoryExtractor(client=mock_client),
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
            cap_resp = await c.post(
                "/v1/capture",
                json={
                    "content": "User uses Python for scripts.",
                    "source": {"kind": "chat", "title": "retry test"},
                    "idempotency_key": f"test_retry_{uuid.uuid4()}",
                },
            )
            ev_id = cap_resp.json()["event_id"]

            ext_resp = await c.post("/v1/extract", json={"event_id": ev_id})
            assert ext_resp.status_code == 200
            data = ext_resp.json()
            assert data["memories_created"] == 1


@pytest.mark.asyncio
async def test_valid_empty_output_produces_no_review_queue(monkeypatch):
    """Verify that a legitimate empty extraction (e.g. conversational banter) returns 200 with 0 memories and no error."""
    async def mock_handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "message": {"content": json.dumps({"entities": [], "memories": []})},
                "done_reason": "stop",
            },
        )

    mock_client = OllamaClient(
        base_url="http://mock-ollama",
        transport=httpx.MockTransport(mock_handler),
    )
    monkeypatch.setattr(
        "app.api.v1.extract.MemoryExtractor",
        lambda *args, **kwargs: MemoryExtractor(client=mock_client),
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
            cap_resp = await c.post(
                "/v1/capture",
                json={
                    "content": "Good morning Jarvis! Just saying hi.",
                    "source": {"kind": "chat", "title": "banter"},
                    "idempotency_key": f"test_banter_{uuid.uuid4()}",
                },
            )
            ev_id = cap_resp.json()["event_id"]

            ext_resp = await c.post("/v1/extract", json={"event_id": ev_id})
            assert ext_resp.status_code == 200
            data = ext_resp.json()
            assert data["memories_created"] == 0

            # Ensure review_queue has NO entries
            pool = app.state.db_pool
            async with pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT count(*) FROM review_queue WHERE proposal->>'event_id' = $1",
                    ev_id,
                )
                assert count == 0


@pytest.mark.asyncio
async def test_retry_extraction_resolves_review_queue(monkeypatch):
    """Verify that POST /v1/extract/{event_id}/retry re-runs extraction and resolves review_queue."""
    should_fail = True

    async def mock_handler(request: httpx.Request):
        nonlocal should_fail
        if should_fail:
            return httpx.Response(
                200,
                json={"message": {"content": "Not JSON at all"}, "done_reason": "stop"},
            )
        valid_json = {
            "entities": [{"ref": "e1", "name": "Postgres", "kind": "tool"}],
            "memories": [{
                "ref": "m1",
                "type": "fact",
                "epistemic": "fact",
                "asserted_by": "user",
                "statement": "Lumen uses Postgres",
                "subject": "e1",
                "predicate": "uses",
                "object": None,
                "object_value": "Postgres",
                "cardinality": "many",
                "polarity": True,
                "evidence": ["uses Postgres"],
                "confidence": 0.95,
                "importance": 0.8,
            }],
        }
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(valid_json)}, "done_reason": "stop"},
        )

    mock_client = OllamaClient(
        base_url="http://mock-ollama",
        transport=httpx.MockTransport(mock_handler),
    )
    monkeypatch.setattr(
        "app.api.v1.extract.MemoryExtractor",
        lambda *args, **kwargs: MemoryExtractor(client=mock_client),
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
            # 1. Capture event
            cap_resp = await c.post(
                "/v1/capture",
                json={
                    "content": "Lumen uses Postgres as primary store.",
                    "source": {"kind": "chat", "title": "retry test"},
                    "idempotency_key": f"test_retry_flow_{uuid.uuid4()}",
                },
            )
            ev_id = cap_resp.json()["event_id"]

            # 2. Extract fails with 422
            fail_resp = await c.post("/v1/extract", json={"event_id": ev_id})
            assert fail_resp.status_code == 422

            pool = app.state.db_pool
            async with pool.acquire() as conn:
                status_before = await conn.fetchval(
                    "SELECT status FROM review_queue WHERE kind = 'failed_extraction' AND proposal->>'event_id' = $1",
                    ev_id,
                )
                assert status_before == "open"

            # 3. Enable success on retry
            should_fail = False

            # 4. Call POST /v1/extract/{event_id}/retry
            retry_resp = await c.post(f"/v1/extract/{ev_id}/retry")
            assert retry_resp.status_code == 200
            data = retry_resp.json()
            assert data["memories_created"] == 1

            # 5. Verify review_queue item is now resolved/accepted
            async with pool.acquire() as conn:
                status_after = await conn.fetchval(
                    "SELECT status FROM review_queue WHERE kind = 'failed_extraction' AND proposal->>'event_id' = $1",
                    ev_id,
                )
                assert status_after == "accepted"

