"""
Query Understanding & Planning Engine (Milestone Q1).
Parses query intent (factual, temporal, preference, consensus, multi_hop),
extracts relative/absolute time ranges via dateparser, splits multi-hop sub-queries,
and resolves coreferences with a zero-latency rule-based fast path.
"""
from __future__ import annotations
from calendar import monthrange
from datetime import datetime, timedelta, timezone
import json
import logging
import re
from typing import Any, Literal, Optional
import dateparser
from pydantic import BaseModel, Field

from app.core.config import settings
from app.adapters.llm.ollama_client import OllamaClient

logger = logging.getLogger("lumen.domain.query_plan")

QueryIntent = Literal["factual", "temporal", "preference", "consensus", "multi_hop"]


class QueryPlan(BaseModel):
    original: str
    rewritten: str
    intent: QueryIntent = "factual"
    sub_queries: list[str] = Field(default_factory=list)
    time_range: Optional[tuple[datetime, datetime]] = None
    as_of: Optional[datetime] = None
    entities: list[str] = Field(default_factory=list)
    type_hints: list[str] = Field(default_factory=list)
    used_llm: bool = False


_CONSENSUS_RE = re.compile(r"\b(agree|disagree|consensus|contradict|conflict|different views)\b", re.IGNORECASE)
_PREF_RE = re.compile(r"\b(prefer|preference|favorite|favourite|like to|style|guideline|directive)\b", re.IGNORECASE)
_AS_OF_RE = re.compile(r"(?:what did you (?:think|believe|know)|as of)\s+(?:on\s+)?([A-Za-z0-9,\s/-]+?)(?:\?|$)", re.IGNORECASE)
_MONTH_RE = re.compile(r"\bin\s+(January|February|March|April|May|June|July|August|September|October|November|December)(?:\s+(\d{4}))?\b", re.IGNORECASE)
_AGO_RE = re.compile(r"\b(\d+)\s+(day|days|week|weeks|month|months)\s+ago\b", re.IGNORECASE)
_PRONOUN_RE = re.compile(r"\b(he|she|they|it|him|her|them|that project|this project|that tool)\b", re.IGNORECASE)
_CAPITALIZED_ENTITY_RE = re.compile(r"\b([A-Z][a-zA-Z0-9_-]{1,}(?:\s+[A-Z][a-zA-Z0-9_-]{1,})*)\b")

_STOPWORDS_ENT = {
    "What", "Where", "When", "Who", "Why", "How", "Which", "Does", "Did", "Do",
    "Is", "Are", "Was", "Were", "Can", "Could", "Would", "Should", "Tell", "Show",
    "In", "On", "At", "The", "My", "Your", "Our", "I", "We", "You",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
}


class QueryPlanner:
    """Fast rule-based query planner with optional Ollama coreference/intent fallback."""

    def __init__(self, ollama_client: Optional[OllamaClient] = None):
        self.ollama_client = ollama_client or OllamaClient(base_url=settings.OLLAMA_BASE_URL)

    def _extract_entities(self, text: str) -> list[str]:
        found = []
        for m in _CAPITALIZED_ENTITY_RE.finditer(text):
            cand = m.group(1).strip()
            if cand not in _STOPWORDS_ENT and cand not in found:
                found.append(cand)
        return found

    def _parse_time_filters(
        self,
        query: str,
        now: datetime,
    ) -> tuple[Optional[tuple[datetime, datetime]], Optional[datetime]]:
        q_low = query.lower()
        as_of: Optional[datetime] = None
        time_range: Optional[tuple[datetime, datetime]] = None

        # 1. Check explicit as_of ("what did you believe on 1 May" / "as of March 10")
        m_as_of = _AS_OF_RE.search(query)
        if m_as_of:
            phrase = m_as_of.group(1).strip()
            dt = dateparser.parse(phrase, settings={"RELATIVE_BASE": now, "TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True})
            if dt:
                as_of = dt
                return None, as_of

        # 2. Check relative shortcuts
        if "yesterday" in q_low:
            y = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            time_range = (y, y + timedelta(days=1) - timedelta(microseconds=1))
            return time_range, None

        if "last week" in q_low:
            start = now - timedelta(days=7)
            return (start, now), None

        if "last month" in q_low:
            first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_prev = first_this - timedelta(microseconds=1)
            first_prev = last_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return (first_prev, last_prev), None

        m_ago = _AGO_RE.search(q_low)
        if m_ago:
            qty = int(m_ago.group(1))
            unit = m_ago.group(2).lower()
            days = qty * (30 if "month" in unit else 7 if "week" in unit else 1)
            target = now - timedelta(days=days)
            return (target - timedelta(days=2), target + timedelta(days=2)), None

        m_mon = _MONTH_RE.search(query)
        if m_mon:
            month_name = m_mon.group(1)
            year = int(m_mon.group(2)) if m_mon.group(2) else now.year
            dt = dateparser.parse(f"1 {month_name} {year}", settings={"TIMEZONE": "UTC", "RETURN_AS_TIMEZONE_AWARE": True})
            if dt:
                if dt > now and not m_mon.group(2):
                    dt = dt.replace(year=year - 1)
                _, last_day = monthrange(dt.year, dt.month)
                start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                end = dt.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
                return (start, end), None

        return None, None

    def _decompose_sub_queries(self, query: str) -> list[str]:
        # Split on " and " when both halves look like clauses/questions
        if " and " in query.lower() and len(query.split()) >= 6:
            parts = [p.strip(" ?.") for p in re.split(r"\s+and\s+", query, flags=re.IGNORECASE) if p.strip()]
            if len(parts) > 1 and all(len(p.split()) >= 2 for p in parts):
                return parts
        return []

    async def plan(
        self,
        query: str,
        *,
        now: Optional[datetime] = None,
        recent_turns: Optional[list[str]] = None,
        cited_entities: Optional[list[str]] = None,
    ) -> QueryPlan:
        """
        Builds a QueryPlan. Skips LLM entirely for short factual/temporal/preference queries
        and only invokes local LLM for pronoun coreference when context turns are provided.
        """
        current_now = now or datetime.now(timezone.utc)
        cleaned = query.strip()
        rewritten = cleaned
        used_llm = False

        # Coreference resolution (only if pronouns exist AND history is provided)
        if _PRONOUN_RE.search(cleaned) and (recent_turns or cited_entities):
            rewritten, used_llm = await self._resolve_coref(cleaned, recent_turns or [], cited_entities or [])

        time_range, as_of = self._parse_time_filters(rewritten, current_now)
        sub_queries = self._decompose_sub_queries(rewritten)
        entities = self._extract_entities(rewritten)

        intent: QueryIntent = "factual"
        type_hints: list[str] = []

        if _CONSENSUS_RE.search(rewritten):
            intent = "consensus"
            type_hints = ["fact", "belief", "decision"]
        elif _PREF_RE.search(rewritten):
            intent = "preference"
            type_hints = ["preference", "constraint", "decision"]
        elif time_range is not None or as_of is not None:
            intent = "temporal"
        elif sub_queries:
            intent = "multi_hop"

        return QueryPlan(
            original=query,
            rewritten=rewritten,
            intent=intent,
            sub_queries=sub_queries,
            time_range=time_range,
            as_of=as_of,
            entities=entities,
            type_hints=type_hints,
            used_llm=used_llm,
        )

    async def _resolve_coref(
        self,
        query: str,
        recent_turns: list[str],
        cited_entities: list[str],
    ) -> tuple[str, bool]:
        context_str = "\n".join(recent_turns[-3:])
        ent_str = ", ".join(cited_entities)
        prompt = (
            f"Recent turns:\n{context_str}\n"
            f"Entities: {ent_str}\n"
            f"User query: {query}\n\n"
            'Rewrite the query replacing pronouns (he/she/it/they/that project) with the explicit entity name. '
            'Output JSON: {"rewritten": "..."}'
        )
        try:
            resp = await self.ollama_client.chat(
                model=settings.LUMEN_LLM_MODEL,
                messages=[
                    {"role": "system", "content": "Resolve coreferences concisely. Output JSON only."},
                    {"role": "user", "content": prompt},
                ],
                format="json",
                options={"temperature": 0.0, "num_predict": 80},
            )
            data = json.loads(resp.get("content", "{}"))
            rw = str(data.get("rewritten") or "").strip()
            if rw:
                return rw, True
        except Exception as e:
            logger.debug(f"Coref LLM fallback: {e}")

        # Deterministic heuristic fallback if only 1 cited entity
        if len(cited_entities) == 1:
            return _PRONOUN_RE.sub(cited_entities[0], query), False
        return query, False
