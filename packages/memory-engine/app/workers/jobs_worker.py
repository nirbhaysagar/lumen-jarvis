"""
Durable PostgreSQL background jobs worker for Lumen Memory Engine.
Processes queued jobs (extract, embed, consolidate) using FOR UPDATE SKIP LOCKED concurrency.
Supports exponential backoff, dead-letter staging on 5 attempts, and graceful shutdown.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Optional
import uuid
import asyncpg

from app.core.config import settings
from app.domain.ports import EmbeddingPort
from app.services.extract_service import extract_event

logger = logging.getLogger("lumen.workers.jobs")


class JobsWorker:
    def __init__(
        self,
        pool: asyncpg.Pool,
        embedding_adapter: Optional[EmbeddingPort] = None,
        poll_interval: float = 0.5,
        max_idle_interval: float = 2.0,
    ):
        self.pool = pool
        self.embedding = embedding_adapter
        self.poll_interval = poll_interval
        self.max_idle_interval = max_idle_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start background worker task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self.run_loop())
        logger.info("Durable JobsWorker started.")

    async def stop(self) -> None:
        """Gracefully stop background worker task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Durable JobsWorker stopped.")

    async def process_one_job(self) -> bool:
        """
        Polls and executes a single queued job within a transaction.
        Returns True if a job was found and processed, False if queue is empty.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                job = await conn.fetchrow(
                    """
                    SELECT id, kind, event_id, payload, attempts, run_after
                    FROM jobs
                    WHERE status = 'queued' AND run_after <= now()
                    ORDER BY id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                )
                if not job:
                    return False

                job_id = job["id"]
                kind = job["kind"]
                event_id = job["event_id"]
                attempts = job["attempts"] + 1

                # Mark in-progress
                await conn.execute(
                    "UPDATE jobs SET status = 'running', attempts = $1 WHERE id = $2",
                    attempts,
                    job_id,
                )

            # Process outside the lock transaction to avoid long-held table locks
            try:
                if kind == "extract":
                    if not event_id:
                        raise ValueError("extract job requires event_id")
                    await extract_event(
                        pool=self.pool,
                        embedding_adapter=self.embedding,
                        event_id=event_id,
                    )
                elif kind == "embed":
                    if not event_id:
                        raise ValueError("embed job requires event_id")
                    if self.embedding:
                        async with self.pool.acquire() as chunk_conn:
                            chunks = await chunk_conn.fetch(
                                "SELECT id, text FROM chunks WHERE event_id = $1 AND embedding IS NULL",
                                event_id,
                            )
                            for ch in chunks:
                                vec = await self.embedding.generate_embedding(ch["text"], prefix="search_document")
                                await chunk_conn.execute(
                                    "UPDATE chunks SET embedding = $1 WHERE id = $2",
                                    list(vec),
                                    ch["id"],
                                )
                elif kind == "consolidate":
                    logger.debug(f"Consolidate job {job_id} executed (noop until C1).")
                else:
                    logger.warning(f"Unknown job kind '{kind}' for job {job_id}")
                    raise ValueError(f"Unsupported job kind: {kind}")

                # Mark completed
                async with self.pool.acquire() as update_conn:
                    await update_conn.execute(
                        "UPDATE jobs SET status = 'done', locked_at = NULL WHERE id = $1",
                        job_id,
                    )
                logger.info(f"Job {job_id} [{kind}] completed successfully (attempt {attempts}).")
                return True

            except Exception as e:
                err_msg = str(e)
                logger.error(f"Job {job_id} [{kind}] failed on attempt {attempts}: {err_msg}")
                async with self.pool.acquire() as update_conn:
                    if attempts >= 5:
                        # Mark dead and stage in review_queue
                        await update_conn.execute(
                            "UPDATE jobs SET status = 'dead', last_error = $1, locked_at = NULL WHERE id = $2",
                            err_msg,
                            job_id,
                        )
                        await update_conn.execute(
                            """
                            INSERT INTO review_queue (kind, memory_ids, entity_ids, reason, proposal, status)
                            VALUES ('failed_extraction', '{}', '{}', $1, $2, 'open')
                            """,
                            f"Job {job_id} ({kind}) failed after 5 attempts",
                            json.dumps({
                                "job_id": job_id,
                                "kind": kind,
                                "event_id": str(event_id) if event_id else None,
                                "attempts": attempts,
                                "last_error": err_msg,
                            }),
                        )
                    else:
                        # Exponential backoff: 2^attempts minutes
                        backoff_seconds = (2 ** attempts) * 60
                        next_run = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
                        await update_conn.execute(
                            """
                            UPDATE jobs
                            SET status = 'queued', run_after = $1, last_error = $2, locked_at = NULL
                            WHERE id = $3
                            """,
                            next_run,
                            err_msg,
                            job_id,
                        )
                return True

    async def run_loop(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Main polling loop."""
        idle_sleep = self.poll_interval
        while self._running:
            if stop_event and stop_event.is_set():
                break
            try:
                processed = await self.process_one_job()
                if processed:
                    idle_sleep = self.poll_interval
                else:
                    idle_sleep = min(idle_sleep * 1.5, self.max_idle_interval)
                    await asyncio.sleep(idle_sleep)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in JobsWorker loop: {e}", exc_info=True)
                await asyncio.sleep(self.max_idle_interval)


async def main() -> None:
    """Standalone CLI entrypoint for running the jobs worker."""
    from app.adapters.storage.pg_pool import PostgresPoolManager
    from app.adapters.embeddings.embedding_adapter import OllamaEmbeddingAdapter

    pool_manager = PostgresPoolManager(dsn=settings.database_url)
    await pool_manager.connect()

    embedding_adapter = None
    if settings.EMBEDDING_MODEL:
        embedding_adapter = OllamaEmbeddingAdapter(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.EMBEDDING_MODEL,
            dimension=settings.EMBEDDING_DIMENSION,
        )

    worker = JobsWorker(pool=pool_manager.pool, embedding_adapter=embedding_adapter)
    logger.info("Starting standalone JobsWorker. Press Ctrl+C to terminate.")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in ("SIGINT", "SIGTERM"):
        try:
            import signal
            loop.add_signal_handler(getattr(signal, sig), stop_event.set)
        except (NotImplementedError, AttributeError):
            pass

    worker_task = asyncio.create_task(worker.run_loop(stop_event=stop_event))
    await stop_event.wait()
    await worker.stop()
    await worker_task
    await pool_manager.disconnect()
    logger.info("JobsWorker shutdown complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(main())
