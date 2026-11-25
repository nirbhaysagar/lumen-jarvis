"""Models for Universal Document Ingestion (Milestone I1)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class ParsedDoc:
    text: str
    title: str
    kind: str  # 'file', 'web', 'import'
    metadata: dict[str, Any] = field(default_factory=dict)
    published_at: Optional[datetime] = None
    author: Optional[str] = None
