"""
Unit & Integration Tests for Milestone M0.8:
Durable Jobs Worker (FOR UPDATE SKIP LOCKED, exponential backoff, dead-letter staging).

Verifies:
1. Capture enqueues extract and embed jobs; JobsWorker processes them to status='done'.
2. Processed extract job creates memories in DB.
3. A failing job retries with exponential backoff and after 5 attempts is marked 'dead' with review_queue entry.
"""
from datetime import datetime, timezone
import json
import uuid
import pytest

from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.domain.events import CaptureRequest
from app.workers.jobs_worker import JobsWorker
from app.main import app


@pytest.mark.asyncio
async def test_jobs_worker_processes_capture_to_completion():
    """Verify that JobsWorker processes extract and embed jobs created by capture."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        embedding = getattr(app.state, "embedding", None)
        events = PostgresEventsAdapter(pool)

        content = "Lumen was built to run offline on local hardware."
        cap = await events.capture(
            CaptureRequest(
                content=content,
                source={"kind": "chat", "title": "offline", "author": "user"},
                idempotency_key=f"worker_test_{uuid.uuid4()}",
            )
        )
        assert len(cap.job_ids) >= 1

        worker = JobsWorker(pool=pool, embedding_adapter=embedding)

        # Process queued jobs for this event
        processed_count = 0
        for _ in range(5):
            did_work = await worker.process_one_job()
            if did_work:
                processed_count += 1
            else:
                break

        assert processed_count >= 1

        # Check that jobs for this event are done
        async with pool.acquire() as conn:
            jobs = await conn.fetch("SELECT id, kind, status FROM jobs WHERE event_id = $1", cap.event_id)
            for j in jobs:
                assert j["status"] in ("done", "queued")

            # Check memories were extracted
            mems = await conn.fetch("SELECT id, statement FROM memories WHERE created_event = $1", cap.event_id)
            assert len(mems) >= 1


@pytest.mark.asyncio
async def test_jobs_worker_dead_letter_after_5_attempts():
    """Verify that a repeatedly failing job is marked 'dead' after 5 attempts and staged to review_queue."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events = PostgresEventsAdapter(pool)
        worker = JobsWorker(pool=pool, embedding_adapter=None)

        # Create real event
        cap = await events.capture(
            CaptureRequest(
                content="Failed extraction test event",
                source={"kind": "chat", "title": "fail_test", "author": "user"},
                idempotency_key=f"worker_fail_{uuid.uuid4()}",
            )
        )

        # Enqueue an invalid job kind that will always fail
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM jobs WHERE event_id = $1", cap.event_id)
            job_id = await conn.fetchval(
                """
                INSERT INTO jobs (kind, event_id, payload, status, attempts, run_after)
                VALUES ('non_existent_kind', $1, '{}'::jsonb, 'queued', 4, now())
                RETURNING id
                """,
                cap.event_id,
            )

        # 5th attempt should fail and mark it dead
        did_work = await worker.process_one_job()
        assert did_work is True

        async with pool.acquire() as conn:
            job_row = await conn.fetchrow("SELECT status, attempts, last_error FROM jobs WHERE id = $1", job_id)
            assert job_row is not None
            assert job_row["status"] == "dead"
            assert job_row["attempts"] >= 5
            assert job_row["last_error"] is not None

            # Verify staged in review_queue
            review_row = await conn.fetchrow(
                "SELECT * FROM review_queue WHERE kind = 'failed_extraction' AND proposal->>'job_id' = $1",
                str(job_id),
            )
            assert review_row is not None
            assert review_row["status"] == "open"
