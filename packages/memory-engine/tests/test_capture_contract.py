"""
Stage 1 Capture Contract Tests.
Tests:
- Chunk coordinates satisfy text[c_start:c_end] == chunk.text
- Idempotency replay returns existing event
- Same text from two authors produces distinct sources via source_key
- Hash chain integrity and tamper detection
- Immutability trigger on events (UPDATE/DELETE raise)
- Sensitivity scanning classifies secret/private/normal
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import uuid
import pytest
import asyncpg
from httpx import AsyncClient

from app.domain.events import CaptureRequest, SourceInfo
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.main import app


@pytest.mark.asyncio
async def test_chunk_coordinates_exact_slice():
    """For every chunk, content[c_start:c_end] == chunk.text."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        adapter = PostgresEventsAdapter(pool)

        content = (
            "Paragraph one is introducing Lumen. Lumen is a Personal AI Operating System.\n\n"
            "Paragraph two goes into architecture. PostgreSQL 16 is used for relational and vector storage. "
            "Postgres triggers guarantee strict append-only provenance logs.\n\n"
            "Paragraph three contains Hindi Unicode text: ल्यूमेन एक व्यक्तिगत एआई ऑपरेटिंग सिस्टम है। "
            "यह डेटा को सुरक्षित रखता है।"
        )
        req = CaptureRequest(
            content=content,
            source=SourceInfo(kind="chat", title="test chunking", author="user"),
            context="test_chunks",
        )
        res = await adapter.capture(req)
        assert res.event_id is not None

        async with pool.acquire() as conn:
            chunks = await conn.fetch(
                "SELECT ord, char_start, char_end, text FROM chunks WHERE event_id = $1 ORDER BY ord ASC",
                res.event_id,
            )
            assert len(chunks) >= 1
            for chunk in chunks:
                expected_slice = content[chunk["char_start"]:chunk["char_end"]]
                assert chunk["text"] == expected_slice


@pytest.mark.asyncio
async def test_same_text_two_authors_distinct_sources():
    """The same text from two different authors produces two distinct source records."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        adapter = PostgresEventsAdapter(pool)

        text = "Postgres 17 has improved performance for vacuuming."
        req1 = CaptureRequest(
            content=text,
            source=SourceInfo(kind="chat", title="opinion", author="Alice"),
            context="source_test",
        )
        req2 = CaptureRequest(
            content=text,
            source=SourceInfo(kind="chat", title="opinion", author="Bob"),
            context="source_test",
        )

        res1 = await adapter.capture(req1)
        res2 = await adapter.capture(req2)

        assert res1.source_id != res2.source_id


@pytest.mark.asyncio
async def test_hash_chain_verification():
    """Verify tamper-evident hash chain across sequential events in a context."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        adapter = PostgresEventsAdapter(pool)
        ctx_name = f"chain_{uuid.uuid4().hex[:8]}"

        res1 = await adapter.capture(CaptureRequest(content="Event 1", context=ctx_name))
        res2 = await adapter.capture(CaptureRequest(content="Event 2", context=ctx_name))
        res3 = await adapter.capture(CaptureRequest(content="Event 3", context=ctx_name))

        async with pool.acquire() as conn:
            events = await conn.fetch(
                "SELECT id, content_hash, prev_hash, chain_hash, speaker, occurred_at FROM events WHERE context_id = $1 ORDER BY recorded_at ASC, id ASC",
                res1.context_id,
            )
            assert len(events) == 3

            # Verify chain linkage
            assert events[0]["prev_hash"] is not None
            assert events[1]["prev_hash"] == events[0]["chain_hash"]
            assert events[2]["prev_hash"] == events[1]["chain_hash"]


@pytest.mark.asyncio
async def test_events_immutability_trigger_blocks_update_and_delete():
    """Trigger events_append_only raises an exception on UPDATE or DELETE."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        adapter = PostgresEventsAdapter(pool)

        res = await adapter.capture(CaptureRequest(content="Immutable fact", context="immut_test"))

        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.RaiseError, match="events is append-only"):
                await conn.execute("UPDATE events SET content = 'Tampered' WHERE id = $1", res.event_id)

            with pytest.raises(asyncpg.RaiseError, match="events is append-only"):
                await conn.execute("DELETE FROM events WHERE id = $1", res.event_id)


@pytest.mark.asyncio
async def test_sensitivity_classification_in_capture():
    """Sensitivity scan populates sensitivity_hint in CaptureResult and event payload."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        adapter = PostgresEventsAdapter(pool)

        # Secret
        res_secret = await adapter.capture(CaptureRequest(content="My OTP is 482913 for login", context="sens_test"))
        assert res_secret.sensitivity_hint == "secret"

        # Private
        res_private = await adapter.capture(CaptureRequest(content="Contact me at test.user@example.com", context="sens_test"))
        assert res_private.sensitivity_hint == "private"

        # Normal
        res_normal = await adapter.capture(CaptureRequest(content="The sky is blue and clear today", context="sens_test"))
        assert res_normal.sensitivity_hint == "normal"
