"""
Multi-pass intelligence extraction from memory text.

Runs 5 parallel extraction passes via asyncio.gather:
  - facts — every factual claim with confidence and evidence
  - decisions — every decision with who, why, alternatives, date, confidence
  - goals — every goal with owner, deadline, confidence
  - concepts — every concept/key term with definition, aliases, confidence
  - entities — people, companies, projects, tools with name, type, role

Each pass uses app.services.llm.aextract_json with structured prompts.
Results are stored as Primitive rows linked to the source memory_id.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.primitive import Primitive, PrimitiveType
from app.services.llm import aextract_json

settings = get_settings()

_LOW_CONFIDENCE_THRESHOLD = 0.7


@dataclass
class ExtractionResult:
    """A single extracted item ready to become a Primitive row."""

    primitive_type: PrimitiveType
    title: str
    content: str
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Prompt templates — one system prompt per extraction pass
# ---------------------------------------------------------------------------

_FACTS_SYSTEM = """You are an expert fact-extractor. Your task is to identify every factual claim in the provided text.

Return a JSON object with a single key "items" containing an array of objects. Each object must have:

- "claim" (string): The factual claim, stated as a clear, self-contained sentence.
- "confidence" (float 0–1): How confident you are that this is genuinely asserted in the text. Use 0.9+ for explicit statements, 0.7–0.89 for strong implications, 0.5–0.69 for weak implications, and <0.5 for guesses.
- "evidence" (string): The exact substring from the text that supports this claim. Quote verbatim.

Omit claims that are purely opinion, hypothetical, or rhetorical. Only include claims the author genuinely asserts as facts.

If there are no factual claims, return {"items": []}."""

_DECISIONS_SYSTEM = """You are an expert decision-analyst. Your task is to identify every decision described in the provided text.

Return a JSON object with a single key "items" containing an array of objects. Each object must have:

- "decision" (string): The decision that was made, stated clearly.
- "who" (string | null): Who made the decision. Use null if not specified.
- "why" (string | null): The reason or rationale behind the decision. Use null if not specified.
- "alternatives" (array of strings): What other options were considered. Empty array if none mentioned.
- "date" (string | null): When the decision was made, in ISO format if possible. Use null if not specified.
- "confidence" (float 0–1): How confident you are this is genuinely a decision described in the text.

If there are no decisions, return {"items": []}."""

_GOALS_SYSTEM = """You are an expert goal-tracker. Your task is to identify every goal, objective, target, or desired outcome mentioned in the provided text.

Return a JSON object with a single key "items" containing an array of objects. Each object must have:

- "goal" (string): The goal, stated as a clear, self-contained sentence.
- "owner" (string | null): The person or entity responsible for the goal. Use null if unknown.
- "deadline" (string | null): The deadline or target date. Use null if not specified.
- "confidence" (float 0–1): How confident you are that this goal is genuinely described in the text.

If there are no goals, return {"items": []}."""

_CONCEPTS_SYSTEM = """You are an expert concept-extractor. Your task is to identify every concept, key term, domain-specific idea, or named methodology in the provided text.

Return a JSON object with a single key "items" containing an array of objects. Each object must have:

- "concept" (string): The concept or term name. Use the canonical/preferred form.
- "definition" (string): A concise definition of the concept as used in the text.
- "aliases" (array of strings): Alternative names or abbreviations for this concept mentioned in or implied by the text.
- "confidence" (float 0–1): How confident you are that this concept is meaningfully discussed in the text.

Only include concepts that are substantive (not generic words). If there are no substantive concepts, return {"items": []}."""

_ENTITIES_SYSTEM = """You are an expert entity-extractor. Your task is to identify every person, company, organization, project, tool, or named product mentioned in the provided text.

Return a JSON object with a single key "items" containing an array of objects. Each object must have:

- "name" (string): The entity name.
- "type" (string): One of "person", "company", "organization", "project", "tool", "product".
- "role" (string): The entity's role or relevance in the context of the text.
- "confidence" (float 0–1): How confident you are that this entity is a real, named entity in the text.

Do not include generic references ("the team", "our company") unless a specific name is given. If there are no named entities, return {"items": []}."""

_ASSUMPTIONS_SYSTEM = """You are an expert assumption-extractor. Your task is to identify unverified assumptions, premises, hypotheses, or beliefs accepted without direct proof.

Return a JSON object with a single key "items" containing an array of objects. Each object must have:
- "assumption" (string): The assumption or hypothesis stated clearly.
- "context" (string | null): Why this assumption is being made.
- "confidence" (float 0–1): Confidence that this is an active assumption in the text.

If there are no assumptions, return {"items": []}."""

_CONSTRAINTS_SYSTEM = """You are an expert constraint-extractor. Your task is to identify technical, operational, architectural, budget, timeline, or business constraints and limitations.

Return a JSON object with a single key "items" containing an array of objects. Each object must have:
- "constraint" (string): The limitation or constraint stated clearly.
- "category" (string | null): Category such as "technical", "budget", "time", "security", "legal".
- "confidence" (float 0–1): Confidence that this is a genuine constraint.

If there are no constraints, return {"items": []}."""

_ACTION_ITEMS_SYSTEM = """You are an expert task-extractor. Your task is to identify actionable next steps, todos, tasks, or follow-ups.

Return a JSON object with a single key "items" containing an array of objects. Each object must have:
- "task" (string): The action item or task stated clearly.
- "assignee" (string | null): Who is assigned. Use null if unassigned.
- "priority" (string | null): "high", "medium", "low" if indicated.
- "confidence" (float 0–1): Confidence that this is an active action item.

If there are no action items, return {"items": []}."""

_QUESTIONS_SYSTEM = """You are an expert question-extractor. Your task is to identify open questions, unresolved unknowns, ambiguities, or areas flagged for future research.

Return a JSON object with a single key "items" containing an array of objects. Each object must have:
- "question" (string): The open question or unresolved issue.
- "importance" (string | null): Why resolving this question matters.
- "confidence" (float 0–1): Confidence that this is an unresolved question.

If there are no open questions, return {"items": []}."""

# ---------------------------------------------------------------------------
# Pass configuration — maps pass name → primitive type + system prompt
# ---------------------------------------------------------------------------

_PASS_CONFIG = {
    "facts":        (PrimitiveType.FACT,        _FACTS_SYSTEM),
    "decisions":    (PrimitiveType.DECISION,    _DECISIONS_SYSTEM),
    "goals":        (PrimitiveType.GOAL,        _GOALS_SYSTEM),
    "concepts":     (PrimitiveType.INSIGHT,     _CONCEPTS_SYSTEM),
    "entities":     (PrimitiveType.FACT,        _ENTITIES_SYSTEM),
    "assumptions":  (PrimitiveType.ASSUMPTION,  _ASSUMPTIONS_SYSTEM),
    "constraints":  (PrimitiveType.CONSTRAINT,  _CONSTRAINTS_SYSTEM),
    "action_items": (PrimitiveType.ACTION_ITEM, _ACTION_ITEMS_SYSTEM),
    "questions":    (PrimitiveType.QUESTION,    _QUESTIONS_SYSTEM),
}


# ---------------------------------------------------------------------------
# Per-pass extraction
# ---------------------------------------------------------------------------

async def _extract_pass(
    pass_name: str,
    text: str,
    primitive_type: PrimitiveType,
    system_prompt: str,
) -> list[ExtractionResult]:
    """Run a single extraction pass on the given text.

    Sends the system prompt as a system message and the text as the user message.
    Parses the returned JSON and converts items into ExtractionResult objects.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    try:
        data = await aextract_json(messages, temperature=0.1)
    except Exception:
        # If the LLM fails entirely (e.g., bad response), return empty
        return []

    # The model should return {"items": [...]} but we are defensive.
    items: list[dict] = []
    if isinstance(data, dict):
        items = data.get("items", [])
    elif isinstance(data, list):
        items = data

    if not isinstance(items, list):
        return []

    results: list[ExtractionResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        if pass_name == "facts":
            title = item.get("claim", "")
            content = item.get("claim", "")
            confidence = _safe_float(item.get("confidence", 0.5))
            evidence = item.get("evidence", "")
            meta: dict[str, Any] = {"evidence": evidence}
            if confidence < _LOW_CONFIDENCE_THRESHOLD:
                meta["low_confidence"] = True
            if title:
                results.append(ExtractionResult(
                    primitive_type=primitive_type,
                    title=title[:500],
                    content=content,
                    confidence=confidence,
                    metadata=meta,
                ))

        elif pass_name == "decisions":
            decision = item.get("decision", "")
            confidence = _safe_float(item.get("confidence", 0.5))
            meta = {
                "who": item.get("who"),
                "why": item.get("why"),
                "alternatives": item.get("alternatives", []),
                "date": item.get("date"),
            }
            if confidence < _LOW_CONFIDENCE_THRESHOLD:
                meta["low_confidence"] = True
            if decision:
                results.append(ExtractionResult(
                    primitive_type=primitive_type,
                    title=decision[:500],
                    content=decision,
                    confidence=confidence,
                    metadata=meta,
                ))

        elif pass_name == "goals":
            goal = item.get("goal", "")
            confidence = _safe_float(item.get("confidence", 0.5))
            meta = {
                "owner": item.get("owner"),
                "deadline": item.get("deadline"),
            }
            if confidence < _LOW_CONFIDENCE_THRESHOLD:
                meta["low_confidence"] = True
            if goal:
                results.append(ExtractionResult(
                    primitive_type=primitive_type,
                    title=goal[:500],
                    content=goal,
                    confidence=confidence,
                    metadata=meta,
                ))

        elif pass_name == "concepts":
            concept = item.get("concept", "")
            definition = item.get("definition", "")
            confidence = _safe_float(item.get("confidence", 0.5))
            aliases_list = item.get("aliases", [])
            meta = {"definition": definition, "aliases": aliases_list if isinstance(aliases_list, list) else []}
            if confidence < _LOW_CONFIDENCE_THRESHOLD:
                meta["low_confidence"] = True
            if concept:
                results.append(ExtractionResult(
                    primitive_type=primitive_type,
                    title=concept[:500],
                    content=f"{concept}: {definition}" if definition else concept,
                    confidence=confidence,
                    metadata=meta,
                ))

        elif pass_name == "entities":
            name = item.get("name", "")
            confidence = _safe_float(item.get("confidence", 0.5))
            entity_type = item.get("type", "unknown")
            role = item.get("role", "")
            meta = {"entity_type": entity_type, "role": role}
            if confidence < _LOW_CONFIDENCE_THRESHOLD:
                meta["low_confidence"] = True
            if name:
                results.append(ExtractionResult(
                    primitive_type=primitive_type,
                    title=name[:500],
                    content=role if role else name,
                    confidence=confidence,
                    metadata=meta,
                ))

        elif pass_name == "assumptions":
            assumption = item.get("assumption", "")
            confidence = _safe_float(item.get("confidence", 0.6))
            ctx = item.get("context", "")
            meta = {"context": ctx}
            if confidence < _LOW_CONFIDENCE_THRESHOLD:
                meta["low_confidence"] = True
            if assumption:
                results.append(ExtractionResult(
                    primitive_type=primitive_type,
                    title=assumption[:500],
                    content=f"Assumption: {assumption}" + (f" (Context: {ctx})" if ctx else ""),
                    confidence=confidence,
                    metadata=meta,
                ))

        elif pass_name == "constraints":
            constraint = item.get("constraint", "")
            confidence = _safe_float(item.get("confidence", 0.7))
            category = item.get("category", "general")
            meta = {"category": category}
            if confidence < _LOW_CONFIDENCE_THRESHOLD:
                meta["low_confidence"] = True
            if constraint:
                results.append(ExtractionResult(
                    primitive_type=primitive_type,
                    title=constraint[:500],
                    content=f"[{category.upper()}] {constraint}" if category else constraint,
                    confidence=confidence,
                    metadata=meta,
                ))

        elif pass_name == "action_items":
            task = item.get("task", "")
            confidence = _safe_float(item.get("confidence", 0.8))
            assignee = item.get("assignee")
            priority = item.get("priority", "medium")
            meta = {"assignee": assignee, "priority": priority}
            if confidence < _LOW_CONFIDENCE_THRESHOLD:
                meta["low_confidence"] = True
            if task:
                results.append(ExtractionResult(
                    primitive_type=primitive_type,
                    title=task[:500],
                    content=f"TODO ({priority}): {task}" + (f" @{assignee}" if assignee else ""),
                    confidence=confidence,
                    metadata=meta,
                ))

        elif pass_name == "questions":
            question = item.get("question", "")
            confidence = _safe_float(item.get("confidence", 0.7))
            imp = item.get("importance", "")
            meta = {"importance_context": imp}
            if confidence < _LOW_CONFIDENCE_THRESHOLD:
                meta["low_confidence"] = True
            if question:
                results.append(ExtractionResult(
                    primitive_type=primitive_type,
                    title=question[:500],
                    content=f"Open Question: {question}" + (f" (Why: {imp})" if imp else ""),
                    confidence=confidence,
                    metadata=meta,
                ))

    return results


def compute_memory_importance(results: list[ExtractionResult], text_length: int = 0) -> float:
    """Compute a dynamic importance score (0.1 to 1.0) based on extracted primitives."""
    if not results:
        # Fallback based on text length
        return min(0.6, max(0.2, text_length / 2000.0))

    # Weight by primitive type significance
    weights = {
        PrimitiveType.DECISION: 0.95,
        PrimitiveType.CONSTRAINT: 0.90,
        PrimitiveType.GOAL: 0.85,
        PrimitiveType.ACTION_ITEM: 0.80,
        PrimitiveType.FACT: 0.70,
        PrimitiveType.INSIGHT: 0.75,
        PrimitiveType.ASSUMPTION: 0.65,
        PrimitiveType.QUESTION: 0.60,
    }

    scores = [weights.get(r.primitive_type, 0.5) * r.confidence for r in results]
    avg_score = sum(scores) / len(scores)

    # Bonus for variety of extracted intelligence
    unique_types = {r.primitive_type for r in results}
    variety_bonus = min(0.2, len(unique_types) * 0.04)

    return round(min(1.0, max(0.1, avg_score + variety_bonus)), 3)


def _safe_float(val: Any) -> float:
    """Coerce a value to a float in [0, 1], defaulting to 0.5 on failure."""
    try:
        f = float(val)
        return max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return 0.5


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------

async def extract_from_memory(
    text: str,
    memory_id: uuid.UUID,
) -> list[ExtractionResult]:
    """Run all 9 extraction passes in parallel against the given text.

    Returns a flat list of ExtractionResult objects ready for storage.
    """
    tasks = [
        _extract_pass(name, text, pt, sp)
        for name, (pt, sp) in _PASS_CONFIG.items()
    ]

    gathered: list[list[ExtractionResult]] = await asyncio.gather(*tasks)

    all_results: list[ExtractionResult] = []
    for result_list in gathered:
        all_results.extend(result_list)

    return all_results


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

async def store_primitives(
    session: AsyncSession,
    results: list[ExtractionResult],
    memory_id: uuid.UUID,
) -> int:
    """Persist a list of ExtractionResult objects as Primitive rows.

    Uses the provided AsyncSession and returns the number of rows stored.
    """
    now = datetime.now(timezone.utc)
    count = 0

    for result in results:
        primitive = Primitive(
            id=uuid.uuid4(),
            memory_id=memory_id,
            primitive_type=result.primitive_type,
            title=result.title,
            content=result.content,
            confidence=result.confidence,
            metadata_=result.metadata,
            created_at=now,
        )
        session.add(primitive)
        count += 1

    await session.commit()
    return count
