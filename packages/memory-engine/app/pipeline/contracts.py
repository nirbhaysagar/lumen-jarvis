"""
Pipeline contract data structures for the 5-stage save pipeline.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
import uuid
from pydantic import BaseModel, Field

MemoryLane = Literal["personal", "work", "code", "chat"]
SensitivityLevel = Literal["normal", "private", "secret"]
EpistemicType = Literal["fact", "belief", "inference", "decision"]


@dataclass
class ExtractedEntity:
    ref: str
    name: str
    kind: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class ExtractedMemory:
    ref: str
    type: str
    epistemic: EpistemicType
    asserted_by: str
    statement: str
    subject_ref: str | None = None
    predicate: str | None = None
    object_ref: str | None = None
    object_value: Any = None
    attributes: dict[str, Any] | None = None
    cardinality: str = "many"
    polarity: bool = True
    valid_from_text: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.8
    importance: float = 0.5
    change_cue: bool = False
    relations: list[dict[str, str]] = field(default_factory=list)
    lane: MemoryLane = "personal"
    sensitivity: SensitivityLevel = "normal"
    about_speaker: bool = False


@dataclass
class VerifiedMemory:
    extracted: ExtractedMemory
    char_start: int
    char_end: int
    quote: str
    chunk_id: uuid.UUID | None = None
    grounding_passed: bool = True


@dataclass
class DroppedMemory:
    statement: str
    reason: str


class SaveReport(BaseModel):
    event_id: uuid.UUID
    stage_ms: dict[str, int]
    counts: dict[str, int]
    dropped: list[dict[str, str]] = Field(default_factory=list)
    memories: list[dict[str, Any]] = Field(default_factory=list)
    review_ids: list[uuid.UUID] = Field(default_factory=list)
    outbox_ids: list[int] = Field(default_factory=list)
