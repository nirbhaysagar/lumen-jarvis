"""
Overnight Consolidation Daemon for Lumen (The Super-Brain).
Periodically executes knowledge graph contradiction patrols, clusters semantic orphans,
and synthesizes the daily Morning Intelligence Digest into vault/digests/.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..memory_client.client import MemoryServiceClient
from ..vault.vault_sync import ObsidianVaultSync
from ..concurrency.supervisor import TaskSupervisor

logger = logging.getLogger("lumen.consolidation_daemon")


class ConsolidationDaemon:
    """
    Autonomous background daemon executing memory consolidation and digest compilation.
    """

    def __init__(
        self,
        memory_client: MemoryServiceClient | None = None,
        vault_sync: ObsidianVaultSync | None = None,
        supervisor: TaskSupervisor | None = None,
        interval_seconds: float = 3600.0 * 24.0, # Default: once every 24 hours
    ) -> None:
        self.memory = memory_client or MemoryServiceClient()
        self.vault = vault_sync or ObsidianVaultSync()
        self.supervisor = supervisor
        self.interval_seconds = interval_seconds

        self._running = False
        self._daemon_task: asyncio.Task | None = None
        self.last_run_time: datetime | None = None
        self.last_report: dict[str, Any] | None = None
        self.last_digest_path: Path | None = None

    async def start(self) -> None:
        """Starts the overnight consolidation daemon background loop."""
        if self._running:
            return
        self._running = True
        logger.info(f"Consolidation Daemon active (Interval: {self.interval_seconds}s).")
        self._daemon_task = asyncio.create_task(self._schedule_loop())

    async def stop(self) -> None:
        """Gracefully halts the consolidation daemon."""
        self._running = False
        if self._daemon_task:
            self._daemon_task.cancel()
            try:
                await self._daemon_task
            except asyncio.CancelledError:
                pass
        logger.info("Consolidation Daemon stopped.")

    async def _schedule_loop(self) -> None:
        """Periodic loop waiting for interval to trigger consolidation."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                if self._running:
                    await self.run_consolidation_job()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in consolidation daemon loop: {e}", exc_info=True)
                await asyncio.sleep(60.0)

    async def run_consolidation_job(self) -> dict[str, Any]:
        """
        Executes a single end-to-end consolidation run:
        1. Calls Memory Engine /graph/consolidate (patrols conflicts, collisions, aliases).
        2. Retrieves pending proposals from the review queue.
        3. Gathers active background task telemetry.
        4. Writes Morning Intelligence Digest to vault/digests/digest_YYYY-MM-DD.md.
        """
        logger.info("Running consolidation job...")
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 1. Run Memory Engine consolidation patrol
        report = await self.memory.run_consolidation() or {
            "conflicts_detected": 0,
            "alias_candidates_found": 0,
            "orphans_detected": 0,
            "proposals_staged": 0,
            "details": {},
        }

        # 2. Fetch pending proposals requiring operator approval
        pending_proposals = await self.memory.list_review_queue(status="PENDING", limit=50)

        # 3. Gather background task checkpoints if supervisor is available
        active_tasks = []
        if self.supervisor and hasattr(self.supervisor, "list_active_tasks"):
            try:
                active_tasks = await self.supervisor.list_active_tasks()
            except Exception:
                pass

        # 4. Generate Morning Intelligence Digest in Obsidian Vault
        digest_path = self.vault.export_digest(
            date_str=date_str,
            report=report,
            pending_proposals=pending_proposals,
            active_tasks=active_tasks,
        )

        self.last_run_time = datetime.now(timezone.utc)
        self.last_report = report
        self.last_digest_path = digest_path

        logger.info(f"Morning Intelligence Digest exported to {digest_path}.")
        return {
            "status": "SUCCESS",
            "date": date_str,
            "digest_path": str(digest_path),
            "report": report,
            "pending_proposals_count": len(pending_proposals),
        }
