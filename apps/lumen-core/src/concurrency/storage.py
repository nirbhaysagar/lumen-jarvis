"""
Checkpoint Storage Adapter for Task Supervisor.
Supports PostgreSQL durable persistence with graceful fallback to In-Memory storage.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Protocol
import asyncpg
from .models import TaskCheckpoint, TaskState

logger = logging.getLogger("lumen.concurrency.storage")


class CheckpointStoragePort(Protocol):
    async def save_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        ...

    async def get_checkpoint(self, task_id: str) -> TaskCheckpoint | None:
        ...

    async def list_checkpoints(self, conversation_id: str | None = None) -> list[TaskCheckpoint]:
        ...


class InMemoryCheckpointStorage:
    def __init__(self) -> None:
        self._store: dict[str, TaskCheckpoint] = {}

    async def save_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        self._store[checkpoint.task_id] = checkpoint

    async def get_checkpoint(self, task_id: str) -> TaskCheckpoint | None:
        return self._store.get(task_id)

    async def list_checkpoints(self, conversation_id: str | None = None) -> list[TaskCheckpoint]:
        if conversation_id:
            return [cp for cp in self._store.values() if cp.conversation_id == conversation_id]
        return list(self._store.values())


class PostgresCheckpointStorage:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.getenv(
            "POSTGRES_DSN",
            "postgresql://lumen:lumen_secure_pass@localhost:5432/lumen_memory",
        )
        self.pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=1,
                max_size=5,
                timeout=5.0,
            )

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def save_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        if not self.pool:
            await self.init()
        assert self.pool is not None

        query = """
        INSERT INTO task_checkpoints (
            task_id, name, conversation_id, state, progress, status_message, result, error, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, NOW()
        )
        ON CONFLICT (task_id) DO UPDATE SET
            state = EXCLUDED.state,
            progress = EXCLUDED.progress,
            status_message = EXCLUDED.status_message,
            result = EXCLUDED.result,
            error = EXCLUDED.error,
            updated_at = NOW();
        """
        result_json = json.dumps(checkpoint.result) if checkpoint.result is not None else None
        state_str = checkpoint.state.value if isinstance(checkpoint.state, TaskState) else str(checkpoint.state)

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                checkpoint.task_id,
                checkpoint.name,
                checkpoint.conversation_id,
                state_str,
                checkpoint.progress,
                checkpoint.status_message,
                result_json,
                checkpoint.error,
            )

    async def get_checkpoint(self, task_id: str) -> TaskCheckpoint | None:
        if not self.pool:
            await self.init()
        assert self.pool is not None

        query = """
        SELECT task_id, name, conversation_id, state, progress, status_message, result, error, created_at, updated_at
        FROM task_checkpoints
        WHERE task_id = $1;
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, task_id)
            if not row:
                return None
            return self._row_to_checkpoint(row)

    async def list_checkpoints(self, conversation_id: str | None = None) -> list[TaskCheckpoint]:
        if not self.pool:
            await self.init()
        assert self.pool is not None

        if conversation_id:
            query = """
            SELECT task_id, name, conversation_id, state, progress, status_message, result, error, created_at, updated_at
            FROM task_checkpoints
            WHERE conversation_id = $1
            ORDER BY created_at DESC;
            """
            params = [conversation_id]
        else:
            query = """
            SELECT task_id, name, conversation_id, state, progress, status_message, result, error, created_at, updated_at
            FROM task_checkpoints
            ORDER BY created_at DESC;
            """
            params = []

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [self._row_to_checkpoint(row) for row in rows]

    def _row_to_checkpoint(self, row: asyncpg.Record) -> TaskCheckpoint:
        res = row["result"]
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except Exception:
                pass

        return TaskCheckpoint(
            task_id=row["task_id"],
            name=row["name"],
            conversation_id=row["conversation_id"],
            state=TaskState(row["state"]),
            progress=row["progress"] or 0.0,
            status_message=row["status_message"],
            result=res,
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
