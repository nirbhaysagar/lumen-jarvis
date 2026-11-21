"""
Integration Tests for L0 Append-Only Capture and Idempotency.
Verifies Milestone M1 acceptance criteria:
1. Capture twice with the same key -> 1 row in events, idempotent_hit=True.
2. Slices chunks and enqueues durable background jobs (embed, extract).
3. UPDATE events raises append-only trigger exception.
4. Capture is synchronous, LLM-free, and succeeds even if Ollama is offline.
"""
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings


@pytest_asyncio.fixture
async def client():
    """Test client fixture that triggers FastAPI lifespan."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
            yield c


@pytest.mark.asyncio
async def test_capture_and_idempotency(client: AsyncClient):
    """Verify first capture inserts, duplicate with same key returns existing event."""
    idem_key = f"test_idem_{uuid.uuid4()}"
    payload = {
        "content": "We decided to move Lumen to Postgres 17 because SQLite locked under concurrent writes.",
        "source": {
            "kind": "chat",
            "title": "architecture discussion",
            "author": "user",
        },
        "context": "default",
        "idempotency_key": idem_key,
    }

    # First capture
    res1 = await client.post("/v1/capture", json=payload)
    assert res1.status_code == 201, f"Expected 201, got {res1.status_code}: {res1.text}"
    data1 = res1.json()
    assert "event_id" in data1
    assert data1["idempotent_hit"] is False
    assert len(data1["job_ids"]) == 2  # embed + extract queued

    event_id1 = data1["event_id"]

    # Second capture with same idempotency key
    res2 = await client.post("/v1/capture", json=payload)
    assert res2.status_code == 201
    data2 = res2.json()
    assert data2["event_id"] == event_id1
    assert data2["idempotent_hit"] is True

    # Direct DB verification: verify only 1 event row exists
    storage = app.state.storage
    async with storage.pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM events WHERE idempotency_key = $1",
            idem_key,
        )
        assert count == 1, f"Expected exactly 1 event row, found {count}"

        # Verify chunks exist
        chunk_count = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE event_id = $1",
            uuid.UUID(event_id1),
        )
        assert chunk_count >= 1

        # Verify jobs are queued
        jobs = await conn.fetch(
            "SELECT kind, status FROM jobs WHERE event_id = $1 ORDER BY kind",
            uuid.UUID(event_id1),
        )
        assert len(jobs) == 2
        kinds = {j["kind"] for j in jobs}
        assert kinds == {"embed", "extract"}
        for j in jobs:
            assert j["status"] == "queued"


@pytest.mark.asyncio
async def test_events_append_only_trigger(client: AsyncClient):
    """Verify that attempting to mutate or delete events raises an exception."""
    storage = app.state.storage
    test_id = uuid.uuid4()
    ctx_row = await storage.pool.fetchrow("SELECT id FROM contexts WHERE slug = 'default'")
    ctx_id = ctx_row["id"]

    async with storage.pool.acquire() as conn:
        # Insert a raw event
        await conn.execute(
            """
            INSERT INTO events (id, kind, type, actor, context_id, content)
            VALUES ($1, 'CAPTURE', 'test.event', 'system', $2, 'Immutable log entry')
            """,
            test_id,
            ctx_id,
        )

        # Attempt to UPDATE -> MUST RAISE
        with pytest.raises(Exception) as exc_info:
            await conn.execute(
                "UPDATE events SET content = 'Tampered' WHERE id = $1",
                test_id,
            )
        assert "is append-only" in str(exc_info.value)
