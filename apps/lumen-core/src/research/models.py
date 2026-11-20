from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone


class SearchMode(str, Enum):
    ALL = "all"
    GENERAL = "general"
    COMMUNITY = "community"
    ACADEMIC = "academic"


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source_type: str = Field(description="web, community_hn, community_reddit, academic_arxiv, or document")
    author: Optional[str] = None
    published_date: Optional[str] = None
    score: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeoptimizedQueries(BaseModel):
    original_prompt: str
    general_query: str
    exact_token_query: str
    community_query: str
    academic_query: str


class ExtractedClaim(BaseModel):
    claim: str
    quote: Optional[str] = None
    author: Optional[str] = None
    source_url: str
    source_type: str
    confidence: float = 0.8
    contradicts_existing: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ResearchDossier(BaseModel):
    topic: str
    summary: str
    key_findings: List[str] = Field(default_factory=list)
    claims: List[ExtractedClaim] = Field(default_factory=list)
    sources: List[SearchResult] = Field(default_factory=list)
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
