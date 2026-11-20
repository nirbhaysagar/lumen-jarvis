"""
Task Supervisor: True Concurrency Spine for Lumen.
Enforces bounded parallel execution (MAX_PARALLEL_TASKS = 4), cooperative cancellation,
Postgres checkpointing, and non-blocking event notifications.
"""
from __future__ import annotations
import asyncio
import inspect
import logging
import uuid
from typing import Any, Callable, Coroutine
from .models import CancellationToken, TaskCheckpoint, TaskState
from .storage import CheckpointStoragePort, InMemoryCheckpointStorage, PostgresCheckpointStorage

logger = logging.getLogger("lumen.concurrency.supervisor")


class TaskSupervisor:
    def __init__(
        self,
        max_parallel_tasks: int = 4,
        storage: CheckpointStoragePort | None = None,
    ) -> None:
        self.max_parallel_tasks = max_parallel_tasks
        self.semaphore = asyncio.Semaphore(max_parallel_tasks)
        self.storage: CheckpointStoragePort = storage or InMemoryCheckpointStorage()

        # In-flight task tracking
        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancellation_tokens: dict[str, CancellationToken] = {}
        self._checkpoints: dict[str, TaskCheckpoint] = {}
        self._notification_queue: asyncio.Queue[TaskCheckpoint] = asyncio.Queue()

    @classmethod
    async def create_with_postgres(
        cls,
        max_parallel_tasks: int = 4,
        dsn: str | None = None,
    ) -> TaskSupervisor:
        """Factory method to initialize with durable PostgreSQL checkpoint storage."""
        storage = PostgresCheckpointStorage(dsn=dsn)
        try:
            await storage.init()
            logger.info("TaskSupervisor initialized with PostgresCheckpointStorage.")
            return cls(max_parallel_tasks=max_parallel_tasks, storage=storage)
        except Exception as e:
            logger.warning(
                f"Failed to connect to Postgres for checkpointing ({e}). Falling back to InMemoryCheckpointStorage."
            )
            return cls(max_parallel_tasks=max_parallel_tasks, storage=InMemoryCheckpointStorage())

    async def spawn_task(
        self,
        name: str,
        coro_fn: Callable[..., Coroutine[Any, Any, Any]],
        conversation_id: str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """
        Dispatches a task immediately to the background worker pool without blocking the caller.
        Returns task_id in sub-millisecond time.
        """
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        token = CancellationToken()
        self._cancellation_tokens[task_id] = token

        checkpoint = TaskCheckpoint(
            task_id=task_id,
            name=name,
            conversation_id=conversation_id,
            state=TaskState.QUEUED,
            progress=0.0,
            status_message="Queued behind concurrency gate",
        )
        self._checkpoints[task_id] = checkpoint
        await self._persist_checkpoint(checkpoint)

        # Dispatch execution wrapper
        async_task = asyncio.create_task(
            self._execute_with_gate(task_id, name, coro_fn, token, checkpoint, *args, **kwargs),
            name=f"lumen-worker-{task_id}",
        )
        self._running_tasks[task_id] = async_task

        return task_id

    async def cancel_task(self, task_id: str) -> bool:
        """
        Cancels a task cooperatively. Signals cancellation token and cancels the asyncio Task.
        """
        token = self._cancellation_tokens.get(task_id)
        task = self._running_tasks.get(task_id)
        checkpoint = self._checkpoints.get(task_id)

        if not token or not checkpoint:
            return False

        token.cancel()
        if task and not task.done():
            task.cancel()

        checkpoint.state = TaskState.CANCELLED
        checkpoint.status_message = "Cancelled by user"
        await self._persist_checkpoint(checkpoint)
        await self._notification_queue.put(checkpoint)
        logger.info(f"Task {task_id} successfully marked CANCELLED.")
        return True

    async def get_task(self, task_id: str) -> TaskCheckpoint | None:
        """Retrieves current checkpoint from memory cache or storage adapter."""
        if task_id in self._checkpoints:
            return self._checkpoints[task_id]
        return await self.storage.get_checkpoint(task_id)

    async def list_tasks(self, conversation_id: str | None = None) -> list[TaskCheckpoint]:
        """Lists all tasks for the given conversation or system-wide."""
        if conversation_id:
            return [
                cp for cp in self._checkpoints.values()
                if cp.conversation_id == conversation_id
            ]
        return list(self._checkpoints.values())

    async def get_next_notification(self, timeout: float | None = None) -> TaskCheckpoint | None:
        """Pulls the next task state update or completion event from the notification queue."""
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._notification_queue.get(), timeout=timeout)
            return await self._notification_queue.get()
        except asyncio.TimeoutError:
            return None

    async def _execute_with_gate(
        self,
        task_id: str,
        name: str,
        coro_fn: Callable[..., Coroutine[Any, Any, Any]],
        token: CancellationToken,
        checkpoint: TaskCheckpoint,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Internal execution wrapper bounded by the concurrency semaphore."""
        try:
            # Wait for available slot in concurrency gate
            async with self.semaphore:
                # Check if task was cancelled while sitting in queue
                if token.is_cancelled:
                    checkpoint.state = TaskState.CANCELLED
                    checkpoint.status_message = "Cancelled before execution began"
                    await self._persist_checkpoint(checkpoint)
                    await self._notification_queue.put(checkpoint)
                    return

                checkpoint.state = TaskState.RUNNING
                checkpoint.status_message = "Executing"
                checkpoint.progress = 0.05
                await self._persist_checkpoint(checkpoint)

                # Prepare progress reporter callback (handles both (percent, msg) and (msg, percent))
                async def progress_reporter(arg1: Any, arg2: Any = None) -> None:
                    token.throw_if_cancelled()
                    if isinstance(arg1, (int, float)):
                        pct = float(arg1)
                        msg = str(arg2) if arg2 is not None else None
                    elif isinstance(arg2, (int, float)):
                        pct = float(arg2)
                        msg = str(arg1) if arg1 is not None else None
                    else:
                        pct = 0.5
                        msg = str(arg1) if arg1 is not None else None

                    if pct > 1.0:
                        pct = pct / 100.0

                    checkpoint.progress = max(0.0, min(1.0, pct))
                    if message := msg:
                        checkpoint.status_message = message
                    await self._persist_checkpoint(checkpoint)

                # Introspect function arguments to inject token and reporter if supported
                sig = inspect.signature(coro_fn)
                call_kwargs = dict(kwargs)
                if "cancellation_token" in sig.parameters:
                    call_kwargs["cancellation_token"] = token
                if "progress_reporter" in sig.parameters:
                    call_kwargs["progress_reporter"] = progress_reporter

                # Execute target coroutine
                result = await coro_fn(*args, **call_kwargs)

                checkpoint.state = TaskState.COMPLETED
                checkpoint.progress = 1.0
                checkpoint.status_message = "Completed successfully"
                if isinstance(result, dict):
                    checkpoint.result = result
                elif result is not None:
                    checkpoint.result = {"output": str(result)}
                await self._persist_checkpoint(checkpoint)
                await self._notification_queue.put(checkpoint)

        except asyncio.CancelledError:
            checkpoint.state = TaskState.CANCELLED
            checkpoint.status_message = "Execution cancelled"
            await self._persist_checkpoint(checkpoint)
            await self._notification_queue.put(checkpoint)

        except Exception as exc:
            logger.exception(f"Task {task_id} failed with error: {exc}")
            checkpoint.state = TaskState.FAILED
            checkpoint.error = str(exc)
            checkpoint.status_message = f"Failed: {exc}"
            await self._persist_checkpoint(checkpoint)
            await self._notification_queue.put(checkpoint)

        finally:
            self._running_tasks.pop(task_id, None)

    async def _persist_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        try:
            await self.storage.save_checkpoint(checkpoint)
        except Exception as e:
            logger.error(f"Failed to persist checkpoint for task {checkpoint.task_id}: {e}")

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Cancels all currently running tasks and closes storage connections."""
        for task_id in list(self._running_tasks.keys()):
            await self.cancel_task(task_id)

        if self._running_tasks:
            tasks = list(self._running_tasks.values())
            await asyncio.wait(tasks, timeout=timeout)

        if hasattr(self.storage, "close"):
            await getattr(self.storage, "close")()
