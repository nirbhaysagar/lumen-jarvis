"""
Pydantic Schemas for Knowledge Consolidation.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel


class ConsolidationReportResponse(BaseModel):
    conflicts_detected: int
    alias_candidates_found: int
    orphans_detected: int
    proposals_staged: int
    timestamp: datetime
    details: dict[str, Any]


class OrphanEntityItem(BaseModel):
    id: str
    canonical_name: str
    category: str
    aliases: list[str]
