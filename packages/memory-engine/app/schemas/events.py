"""
Border Validation Schemas for Inbound Events.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


class CreateEventRequest(BaseModel):
    source: str = Field(..., description="Origin of event: 'user_cli', 'chat', 'tool', 'system'")
    payload: dict = Field(..., description="Raw JSON payload of the event")
    conversation_id: str | None = Field(default=None, description="Optional active conversation thread ID")
    idempotency_key: str | None = Field(default=None, description="Client idempotency key for deduplication")


class EventResponse(BaseModel):
    id: uuid.UUID
    source: str
    conversation_id: str | None
    created_at: datetime
    hash: str
    idempotent_hit: bool = False
