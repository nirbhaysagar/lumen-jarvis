"""
Pydantic Schemas for Transactional Outbox.
"""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class OutboxItem(BaseModel):
    id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    status: str
    retry_count: int
    error: str | None = None
    created_at: datetime
    processed_at: datetime | None = None


class OutboxPendingResponse(BaseModel):
    items: list[OutboxItem]
    count: int


class OutboxStatsResponse(BaseModel):
    pending: int
    processed: int
    failed: int
    total: int


class OutboxFailRequest(BaseModel):
    error: str
