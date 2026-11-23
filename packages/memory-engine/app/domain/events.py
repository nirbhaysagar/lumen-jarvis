"""
Domain models for Lumen Event Log and Synchronous Capture (L0).
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
import uuid
from pydantic import BaseModel, Field


SourceKind = Literal["chat", "voice", "file", "web", "note", "api", "import"]
EventKind = Literal["CAPTURE", "DERIVED", "SYSTEM"]


class SourceInfo(BaseModel):
    kind: SourceKind = "chat"
    uri: str | None = None
    title: str | None = None
    author: str = "user"
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaptureRequest(BaseModel):
    content: str = Field(..., min_length=1)
    source: SourceInfo = Field(default_factory=SourceInfo)
    context: str = "default"
    occurred_at: datetime | None = None
    recorded_at: datetime | None = None
    idempotency_key: str | None = None


class CaptureResult(BaseModel):
    event_id: uuid.UUID
    job_ids: list[int]
    idempotent_hit: bool = False
    context_id: uuid.UUID
    source_id: uuid.UUID
