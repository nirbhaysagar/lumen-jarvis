"""
Knowledge Consolidation Engine for Lumen Memory.
Autonomous contradiction patrols, semantic clustering, and orphan consolidation.
Strictly adheres to Epistemic Integrity: all detected conflicts and merges are staged
in the Review Queue for human confirmation (zero silent overwrite).
"""
from __future__ import annotations
import logging
import uuid
from typing import Any
from datetime import datetime, timezone

from .models import (
    ReviewProposal,
    ProposalStatus,
    ProposalTier,
    ConsolidationReport,
)
from .ports import StoragePort

logger = logging.getLogger("lumen.consolidator")


class KnowledgeConsolidator:
    """
    Overnight memory consolidation service.
    Analyzes the entire knowledge graph for latent contradictions,
    functional collisions, and semantic aliases.
    """

    def __init__(self, storage: StoragePort) -> None:
        self.storage = storage

    async def run_consolidation(self) -> ConsolidationReport:
        """Execute full consolidation patrol and stage proposals in review_queue."""
        logger.info("Starting Knowledge Graph overnight consolidation patrol...")
        proposals_staged = 0

        # Fetch existing pending proposals to prevent duplicate staging
        existing_proposals = await self.storage.list_review_proposals(
            status=ProposalStatus.PENDING,
            limit=500,
        )
        existing_reasons = {p.conflict_reason for p in existing_proposals if p.conflict_reason}

        # ----------------------------------------------------
        # 1. Contradiction Patrol: Opposing Predicates (PREFERS vs DISLIKES)
        # ----------------------------------------------------
        opposing = await self.storage.find_contradictory_triples()
        conflicts_count = len(opposing)

        for opp in opposing:
            reason = (
                f"Opposing predicate collision: '{opp['subject_name']}' both "
                f"{opp['predicate_1']} and {opp['predicate_2']} '{opp['object_name']}'."
            )
            if reason not in existing_reasons:
                proposal = ReviewProposal(
                    proposal_type="CONTRADICTION_UPDATE",
                    subject_raw=opp["subject_name"],
                    predicate_raw=f"{opp['predicate_1']} vs {opp['predicate_2']}",
                    object_raw=opp["object_name"],
                    resolved_subject_id=uuid.UUID(opp["subject_id"]),
                    resolved_object_id=uuid.UUID(opp["object_id"]),
                    confidence=1.0,
                    tier=ProposalTier.TIER_B,
                    status=ProposalStatus.PENDING,
                    conflict_reason=reason,
                    conflicting_triple_id=uuid.UUID(opp["triple_id_1"]),
                )
                await self.storage.stage_review_proposal(proposal)
                proposals_staged += 1
                existing_reasons.add(reason)

        # ----------------------------------------------------
        # 2. Contradiction Patrol: Functional Predicate Collisions
        # ----------------------------------------------------
        collisions = await self.storage.find_multi_value_functional_collisions()
        conflicts_count += len(collisions)

        for col in collisions:
            reason = (
                f"Multi-value collision on functional predicate '{col['predicate']}': "
                f"'{col['subject_name']}' has conflicting active targets: {col['object_names']}."
            )
            if reason not in existing_reasons:
                first_triple_id = uuid.UUID(col["triple_ids"][0]) if col["triple_ids"] else None
                first_obj_id = uuid.UUID(col["object_ids"][0]) if col["object_ids"] else None

                proposal = ReviewProposal(
                    proposal_type="CONTRADICTION_UPDATE",
                    subject_raw=col["subject_name"],
                    predicate_raw=col["predicate"],
                    object_raw=", ".join(col["object_names"]),
                    resolved_subject_id=uuid.UUID(col["subject_id"]),
                    resolved_object_id=first_obj_id,
                    confidence=1.0,
                    tier=ProposalTier.TIER_B,
                    status=ProposalStatus.PENDING,
                    conflict_reason=reason,
                    conflicting_triple_id=first_triple_id,
                )
                await self.storage.stage_review_proposal(proposal)
                proposals_staged += 1
                existing_reasons.add(reason)

        # ----------------------------------------------------
        # 3. Semantic Clustering: High Similarity Alias Candidates
        # ----------------------------------------------------
        alias_pairs = await self.storage.find_high_similarity_entity_pairs(threshold=0.88, limit=30)
        aliases_count = len(alias_pairs)

        for pair in alias_pairs:
            reason = (
                f"High embedding similarity ({pair['similarity']:.2f}) suggests "
                f"'{pair['source_name']}' and '{pair['target_name']}' refer to the same concept."
            )
            if reason not in existing_reasons:
                proposal = ReviewProposal(
                    proposal_type="ALIAS_MERGE",
                    subject_raw=pair["source_name"],
                    predicate_raw="EQUIVALENT_TO",
                    object_raw=pair["target_name"],
                    resolved_subject_id=uuid.UUID(pair["source_id"]),
                    resolved_object_id=uuid.UUID(pair["target_id"]),
                    confidence=pair["similarity"],
                    tier=ProposalTier.TIER_B,
                    status=ProposalStatus.PENDING,
                    conflict_reason=reason,
                )
                await self.storage.stage_review_proposal(proposal)
                proposals_staged += 1
                existing_reasons.add(reason)

        # ----------------------------------------------------
        # 4. Graph Orphans Inventory
        # ----------------------------------------------------
        orphans = await self.storage.get_orphan_entities(max_degree=1)
        orphans_count = len(orphans)

        report = ConsolidationReport(
            conflicts_detected=conflicts_count,
            alias_candidates_found=aliases_count,
            orphans_detected=orphans_count,
            proposals_staged=proposals_staged,
            details={
                "opposing_predicates": opposing,
                "functional_collisions": collisions,
                "alias_candidates": alias_pairs,
                "orphan_entities": [
                    {"id": str(o.id), "name": o.canonical_name, "category": o.category}
                    for o in orphans[:20]
                ],
            },
        )
        logger.info(
            f"Consolidation complete: {conflicts_count} conflicts, "
            f"{aliases_count} alias candidates, {orphans_count} orphans, "
            f"{proposals_staged} new review proposals staged."
        )
        return report
