"""
In-Built Obsidian Knowledge Vault File Watcher (Milestone O2).
Monitors the vault directory using watchdog and triggers asynchronous
two-way reconciliation on note modifications and inbox drops.
"""
from __future__ import annotations
import asyncio
import logging
from pathlib import Path
import time
from typing import Optional
import asyncpg
from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent, FileDeletedEvent
from watchdog.observers import Observer

from app.vault.projector import get_default_vault_dir
from app.vault.reconcile import VaultReconciler

logger = logging.getLogger("lumen.vault.watcher")


class VaultEventHandler(FileSystemEventHandler):
    """Event handler for filesystem changes in the Knowledge Vault."""

    def __init__(self, reconciler: VaultReconciler, loop: asyncio.AbstractEventLoop, debounce_seconds: float = 1.5):
        super().__init__()
        self.reconciler = reconciler
        self.loop = loop
        self.debounce_seconds = debounce_seconds
        self._last_event_time: dict[str, float] = {}

    def _is_ignored(self, path_str: str) -> bool:
        p = Path(path_str)
        if not p.name.endswith(".md"):
            return True
        if p.name.startswith(".tmp_") or p.name.startswith("."):
            return True
        parts = p.parts
        if any(part.startswith(".") for part in parts):
            return True
        if "_conflicts" in parts:
            return True
        return False

    def _should_debounce(self, path_str: str) -> bool:
        now = time.time()
        last = self._last_event_time.get(path_str, 0.0)
        if now - last < self.debounce_seconds:
            return True
        self._last_event_time[path_str] = now
        return False

    def on_modified(self, event):
        if event.is_directory or self._is_ignored(event.src_path):
            return
        if self._should_debounce(event.src_path):
            return
        logger.info(f"Vault file modified: {event.src_path}")
        asyncio.run_coroutine_threadsafe(
            self.reconciler.reconcile_file_change(Path(event.src_path)),
            self.loop,
        )

    def on_created(self, event):
        if event.is_directory or self._is_ignored(event.src_path):
            return
        if self._should_debounce(event.src_path):
            return
        logger.info(f"Vault file created: {event.src_path}")
        asyncio.run_coroutine_threadsafe(
            self.reconciler.reconcile_file_change(Path(event.src_path)),
            self.loop,
        )

    def on_deleted(self, event):
        if event.is_directory or self._is_ignored(event.src_path):
            return
        if self._should_debounce(event.src_path):
            return
        logger.warning(f"Vault file deleted: {event.src_path} (Protection active)")
        asyncio.run_coroutine_threadsafe(
            self.reconciler.reconcile_file_deletion(Path(event.src_path)),
            self.loop,
        )


class VaultWatcherService:
    """Manages the background observer thread for the in-built knowledge vault."""

    def __init__(self, pool: asyncpg.Pool, vault_dir: Optional[Path] = None, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.pool = pool
        self.vault_dir = (vault_dir or get_default_vault_dir()).resolve()
        self.loop = loop or asyncio.get_event_loop()
        self.reconciler = VaultReconciler(self.pool, self.vault_dir)
        self.observer: Optional[Observer] = None

    def start(self) -> None:
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        handler = VaultEventHandler(self.reconciler, self.loop)
        self.observer = Observer()
        self.observer.schedule(handler, str(self.vault_dir), recursive=True)
        self.observer.daemon = True
        self.observer.start()
        logger.info(f"In-built Vault Watcher started monitoring {self.vault_dir}")

    def stop(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=3.0)
            logger.info("In-built Vault Watcher stopped.")


async def start_vault_watcher_cli(vault_dir: Optional[Path] = None) -> None:
    """CLI runner for `lumen-memory vault watch`."""
    from app.core.config import settings
    from app.adapters.storage.pg_pool import PostgresPoolManager

    storage = PostgresPoolManager(dsn=settings.database_url)
    await storage.connect()
    loop = asyncio.get_running_loop()
    service = VaultWatcherService(storage.pool, vault_dir=vault_dir, loop=loop)
    service.start()
    print(f"👁️ Vault Watcher active on {service.vault_dir}. Press Ctrl+C to exit.")
    try:
        while True:
            await asyncio.sleep(1.0)
    except (asyncio.CancelledError, KeyboardInterrupt):
        service.stop()
    finally:
        await storage.disconnect()
