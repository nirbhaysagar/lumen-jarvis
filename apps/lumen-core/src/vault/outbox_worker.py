"""
Transactional Outbox Worker for Obsidian Vault Synchronization.
Listens to PostgreSQL pg_notify on 'memory_outbox_channel' and drains pending outbox rows
into atomic Markdown notes with [[Wikilinks]]. Decouples DB transactions from disk I/O.
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from .vault_sync import ObsidianVaultSync
from ..memory_client.client import MemoryServiceClient

logger = logging.getLogger("lumen.outbox_worker")


class OutboxWorker:
    """
    Background worker that reliably pulls pending events from PostgreSQL's `memory_outbox`
    and renders them to Obsidian Markdown files.
    """

    def __init__(
        self,
        memory_client: MemoryServiceClient | None = None,
        vault_sync: ObsidianVaultSync | None = None,
        postgres_dsn: str | None = None,
        fallback_poll_interval: float = 15.0,
    ) -> None:
        self.client = memory_client or MemoryServiceClient()
        self.vault = vault_sync or ObsidianVaultSync()
        self.postgres_dsn = (
            postgres_dsn
            or os.getenv("DATABASE_URL")
            or f"postgresql://{os.getenv('POSTGRES_USER', 'lumen')}:{os.getenv('POSTGRES_PASSWORD', '')}@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'lumen_memory')}"
        )
        self.fallback_poll_interval = fallback_poll_interval

        self._running = False
        self._notify_event = asyncio.Event()
        self._worker_task: asyncio.Task | None = None
        self._listener_task: asyncio.Task | None = None

        # Telemetry
        self.processed_count: int = 0
        self.failed_count: int = 0
        self.last_sync_time: datetime | None = None

    async def start(self) -> None:
        """Start both the pg_notify listener and the outbox draining loop."""
        if self._running:
            return
        self._running = True
        logger.info("Starting Transactional Outbox Worker...")

        self._listener_task = asyncio.create_task(self._listen_notifications())
        self._worker_task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        """Gracefully stop the outbox worker and listener."""
        self._running = False
        self._notify_event.set()

        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        logger.info("Transactional Outbox Worker stopped.")

    def _on_notification(self, conn: Any, pid: int, channel: str, payload: str) -> None:
        """Callback invoked immediately when PostgreSQL fires pg_notify."""
        logger.debug(f"Received pg_notify on {channel}: {payload}")
        self._notify_event.set()

    async def _listen_notifications(self) -> None:
        """Direct asyncpg listener holding open TCP connection for pg_notify events."""
        import asyncpg

        while self._running:
            conn = None
            try:
                conn = await asyncpg.connect(self.postgres_dsn)
                await conn.add_listener("lumen_outbox", self._on_notification)
                await conn.add_listener("memory_outbox_channel", self._on_notification)
                logger.info("LISTEN lumen_outbox and memory_outbox_channel established.")

                while self._running:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"LISTEN connection error: {e}. Retrying in 5s...")
                await asyncio.sleep(5.0)
            finally:
                if conn:
                    try:
                        await conn.close()
                    except Exception:
                        pass

    async def _drain_loop(self) -> None:
        """Processes pending items when notified or on fallback timeout."""
        # Initial drain on startup
        await self.process_pending_batch()

        while self._running:
            try:
                # Wait for either a push notification or fallback timer
                try:
                    await asyncio.wait_for(
                        self._notify_event.wait(),
                        timeout=self.fallback_poll_interval,
                    )
                except asyncio.TimeoutError:
                    pass  # Fallback timer elapsed, perform safety sweep

                self._notify_event.clear()

                if self._running:
                    await self.process_pending_batch()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in outbox drain loop: {e}", exc_info=True)
                await asyncio.sleep(2.0)

    async def process_pending_batch(self, limit: int = 50) -> int:
        """Fetch and render pending outbox records into Obsidian vault."""
        items = await self.client.list_pending_outbox(limit=limit)
        if not items:
            return 0

        synced = 0
        for item in items:
            item_id = str(item.get("id"))
            agg_type = item.get("aggregate_type", "").upper()
            payload = item.get("payload", {})

            try:
                if agg_type == "FACT":
                    self.vault.export_fact(payload)
                elif agg_type == "ENTITY":
                    self.vault.export_entity(payload)
                elif agg_type == "TRIPLE":
                    self.vault.export_triple(payload)
                elif agg_type == "EPISODE":
                    self.vault.export_episode(
                        session_id=payload.get("session_id", "default"),
                        summary=payload.get("summary", ""),
                        turn_count=payload.get("turn_count", 0),
                        evacuated_turns=payload.get("evacuated_turns"),
                    )
                else:
                    logger.warning(f"Unknown outbox aggregate_type: {agg_type}")

                # Acknowledge processed row in Postgres
                await self.client.ack_outbox_event(item_id)
                self.processed_count += 1
                synced += 1
                self.last_sync_time = datetime.now(timezone.utc)
            except Exception as e:
                logger.error(f"Failed to process outbox item {item_id}: {e}", exc_info=True)
                await self.client.fail_outbox_event(item_id, str(e))
                self.failed_count += 1

        return synced

    def get_status(self) -> dict[str, Any]:
        """Telemetry snapshot for REPL inspection."""
        return {
            "running": self._running,
            "processed_count": self.processed_count,
            "failed_count": self.failed_count,
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "vault_stats": self.vault.get_vault_stats(),
        }
