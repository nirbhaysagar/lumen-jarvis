"""
Pure decision function for memory resolution (Tier A auto-commit vs Tier B human review).
Determines actions: create, supersede, reaffirm, stage.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal
import uuid
from rapidfuzz import fuzz

from app.domain.match import statements_conflict

ActionType = Literal["create", "supersede", "reaffirm", "stage"]
TierType = Literal["A", "B"]


@dataclass
class CandidateMemory:
    statement: str
    subject_id: uuid.UUID | None
    predicate: str | None
    object_id: uuid.UUID | None
    object_value: Any | None
    cardinality: str = "many"
    polarity: bool = True
    change_cue: bool = False
    confidence: float = 0.8
    epistemic: str = "fact"
    asserted_by: str = "user"
    lane: str = "personal"
    sensitivity: str = "normal"
    grounding_passed: bool = True


@dataclass
class ExistingMemory:
    id: uuid.UUID
    lineage_id: uuid.UUID
    version: int
    statement: str
    subject_id: uuid.UUID | None
    predicate: str | None
    object_id: uuid.UUID | None
    object_value: Any | None
    cardinality: str = "many"
    polarity: bool = True
    asserted_by: str = "user"
    status: str = "active"


@dataclass
class Decision:
    action: ActionType
    tier: TierType
    reason: str
    target_lineage: uuid.UUID | None = None
    target_memory_id: uuid.UUID | None = None
    contradicts: list[uuid.UUID] = field(default_factory=list)


def _extract_val(value: Any) -> str:
    if isinstance(value, str):
        try:
            import json
            parsed = json.loads(value)
            if isinstance(parsed, dict) and "value" in parsed:
                return str(parsed["value"]).strip().lower()
        except Exception:
            pass
        return value.strip().lower()
    if isinstance(value, dict) and "value" in value:
        return str(value["value"]).strip().lower()
    return str(value or "").strip().lower()


def decide(
    cand: CandidateMemory,
    own_existing: ExistingMemory | None,
    others_existing: list[ExistingMemory] | None = None,
    auto_commit_min_conf: float = 0.7,
) -> Decision:
    """
    Pure decision function resolving candidate memories:
    - Tier A: auto-commit (create, supersede, reaffirm)
    - Tier B: stage for review (cross-speaker conflict, low confidence, inference, ungrounded value)
    """
    others = others_existing or []

    # 1. Check Grounding
    if not cand.grounding_passed:
        return Decision(
            action="stage",
            tier="B",
            reason="value_not_in_quote",
            target_lineage=own_existing.lineage_id if own_existing else None,
        )

    # 2. Check Inferences
    if cand.epistemic == "inference":
        return Decision(
            action="stage",
            tier="B",
            reason="inference",
            target_lineage=own_existing.lineage_id if own_existing else None,
        )

    # 3. Check Confidence Threshold
    if cand.confidence < auto_commit_min_conf:
        return Decision(
            action="stage",
            tier="B",
            reason="low_confidence",
            target_lineage=own_existing.lineage_id if own_existing else None,
        )

    # 4. Check Cross-Speaker Disagreements
    if others and not own_existing:
        # Check if other speaker's active fact conflicts
        conflicts = []
        for other in others:
            if other.asserted_by.lower() == cand.asserted_by.lower():
                continue
            same_predicate = bool(
                other.predicate and cand.predicate and other.predicate == cand.predicate
            )
            values_differ = (
                other.object_id != cand.object_id
                or _extract_val(other.object_value) != _extract_val(cand.object_value)
                or other.polarity != cand.polarity
            )
            wording_differs = statements_conflict(other.statement, cand.statement)
            if same_predicate and (values_differ or wording_differs):
                conflicts.append(other.id)
            elif not same_predicate and wording_differs:
                conflicts.append(other.id)

        if conflicts:
            return Decision(
                action="stage",
                tier="B",
                reason="cross_speaker_conflict",
                contradicts=conflicts,
            )

    # 5. Check Reaffirmation vs Update on Own Speaker Memory
    if own_existing and own_existing.status == "active":
        if cand.object_id is not None and own_existing.object_id is not None:
            same_object = (cand.object_id == own_existing.object_id)
        else:
            same_object = (cand.object_id == own_existing.object_id) and (_extract_val(cand.object_value) == _extract_val(own_existing.object_value))

        sim = fuzz.ratio(cand.statement.lower(), own_existing.statement.lower())
        if same_object and sim >= 90.0:
            return Decision(
                action="reaffirm",
                tier="A",
                reason="reaffirm",
                target_lineage=own_existing.lineage_id,
                target_memory_id=own_existing.id,
            )

        # Check Negation Supersede
        if not cand.polarity and own_existing.polarity:
            return Decision(
                action="supersede",
                tier="A",
                reason="negation_supersede",
                target_lineage=own_existing.lineage_id,
                target_memory_id=own_existing.id,
            )

        # Check Cardinality One or Change Cue
        if cand.cardinality == "one" or cand.change_cue:
            return Decision(
                action="supersede",
                tier="A",
                reason="same_speaker_update",
                target_lineage=own_existing.lineage_id,
                target_memory_id=own_existing.id,
            )

    # 6. Default: Create New Memory (Tier A)
    return Decision(
        action="create",
        tier="A",
        reason="new",
    )
