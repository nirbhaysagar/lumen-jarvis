"""
Concurrency Domain Models & State Machine.
Defines task lifecycle states, checkpoints, and cooperative cancellation tokens.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class CancellationToken:
    """
    Cooperative cancellation token using asyncio.Event.
    Passed into worker coroutines so they can periodically check if an interruption or user cancel occurred.
    """
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def throw_if_cancelled(self) -> None:
        if self._event.is_set():
            raise asyncio.CancelledError("Task was cancelled via cooperative cancellation token.")


@dataclass
class TaskCheckpoint:
    task_id: str
    name: str
    conversation_id: str | None = None
    state: TaskState = TaskState.QUEUED
    progress: float = 0.0  # 0.0 to 1.0
    status_message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "conversation_id": self.conversation_id,
            "state": self.state.value if isinstance(self.state, TaskState) else self.state,
            "progress": round(self.progress, 3),
            "status_message": self.status_message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
