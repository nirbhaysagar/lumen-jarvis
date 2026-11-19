"""
Entity Resolver & Review Queue Staging Buffer: Ring 2 of Knowledge Graph Integrity.
Implements Two-Tier Entity Resolution, Canonical Alias Merging, Controlled Predicate Normalization,
and Confidence-Tiered Contradiction Gating.
"""
from __future__ import annotations
import logging
import uuid
from typing import Sequence
from .models import (
    Entity,
    Triple,
    ReviewProposal,
    ProposalStatus,
    ProposalTier,
)
from .ports import StoragePort, EmbeddingPort

logger = logging.getLogger("lumen.memory.resolver")


class EntityResolver:
    # Common predicate normalization map
    PREDICATE_SYNONYMS = {
        "CREATED": "FOUNDED",
        "STARTED": "FOUNDED",
        "ESTABLISHED": "FOUNDED",
        "COFOUNDED": "FOUNDED",
        "BUILDS": "DEVELOPS",
        "BUILT": "DEVELOPS",
        "PROGRAMMED": "DEVELOPS",
        "AUTHORED": "DEVELOPS",
        "LOVES": "PREFERS",
        "FAVORS": "PREFERS",
        "CHOSE": "PREFERS",
        "CHOSEN": "PREFERS",
        "HATES": "DISLIKES",
        "REJECTED": "DISLIKES",
        "REJECTS": "DISLIKES",
        "UTILIZES": "USES",
        "EMPLOYING": "USES",
        "RUNS_ON": "DEPENDS_ON",
        "REQUIRES": "DEPENDS_ON",
        "CODED_IN": "WRITTEN_IN",
        "IMPLEMENTED_IN": "WRITTEN_IN",
    }

    # Predicates that are semantically singular / mutually exclusive
    # If a new assertion arrives with a different object, it represents a contradiction/update!
    EXCLUSIVE_PREDICATES = {
        "PREFERS", "DISLIKES", "WRITTEN_IN", "PRIMARY_DATABASE", "FAVORITE"
    }

    def __init__(self, storage: StoragePort, embedder: EmbeddingPort | None = None) -> None:
        self.storage = storage
        self.embedder = embedder

    def normalize_predicate(self, raw_pred: str) -> str:
        """Normalizes free-form verb phrases to canonical predicate ontology."""
        clean = raw_pred.strip().upper().replace(" ", "_").replace("-", "_")
        return self.PREDICATE_SYNONYMS.get(clean, clean)

    async def resolve_or_create_entity(
        self,
        raw_name: str,
        category: str = "concept",
        alias: str | None = None,
    ) -> Entity:
        """
        Two-Tier Entity Resolution:
        Tier 1: Look up exact canonical name or existing aliases array.
        Tier 2: If novel, instantiate new canonical entity with optional embedding.
        """
        clean_name = raw_name.strip()

        # 1. Tier 1: Surface / Alias Match
        existing = await self.storage.find_entity_by_name_or_alias(clean_name)
        if existing:
            # If an alias was supplied and isn't recorded yet, merge it
            if alias and alias.lower() not in [a.lower() for a in existing.aliases]:
                existing.aliases.append(alias)
                await self.storage.save_entity(existing)
            return existing

        # 2. Tier 2: Create new canonical entity node
        embedding = None
        if self.embedder:
            try:
                embedding = await self.embedder.generate_embedding(clean_name)
            except Exception as e:
                logger.warning(f"Embedding generation failed for entity {clean_name}: {e}")

        aliases = [clean_name.lower()]
        if alias and alias.lower() not in aliases:
            aliases.append(alias.lower())

        new_entity = Entity(
            canonical_name=clean_name,
            category=category,
            aliases=aliases,
            embedding=embedding,
        )
        new_id = await self.storage.save_entity(new_entity, embedding=embedding)
        new_entity.id = new_id
        return new_entity

    async def stage_or_commit_triple(
        self,
        subject_raw: str,
        predicate_raw: str,
        object_raw: str,
        source_event_id: uuid.UUID | None = None,
        confidence: float = 1.0,
        subject_category: str = "concept",
        object_category: str = "concept",
    ) -> tuple[ReviewProposal, Triple | None]:
        """
        Evaluates a candidate triple assertion through the Review Queue:
        1. Resolves subject and object entities via alias registry.
        2. Normalizes predicate against controlled ontology.
        3. Audits for contradictions against active memory edges.
        4. Tier A (No contradiction & high confidence) -> Auto-committed to canonical triples.
        5. Tier B (Contradiction detected or ambiguous) -> Staged in review_queue as PENDING.
        """
        # Step 1: Resolve Entities
        subj_entity = await self.resolve_or_create_entity(subject_raw, category=subject_category)
        obj_entity = await self.resolve_or_create_entity(object_raw, category=object_category)

        # Step 2: Normalize Predicate
        canonical_pred = self.normalize_predicate(predicate_raw)

        # Step 3: Conflict & Contradiction Detection
        active_triples = await self.storage.get_active_triples_for_subject(
            subj_entity.id, predicate=canonical_pred
        )

        conflicting_triple = None
        conflict_reason = None
        is_contradiction = False

        if canonical_pred in self.EXCLUSIVE_PREDICATES and active_triples:
            for active in active_triples:
                # If pointing to a different object entity, this is an explicit contradiction/update!
                if active.object_id != obj_entity.id:
                    is_contradiction = True
                    conflicting_triple = active
                    # Fetch old object name for clear human auditing
                    old_obj = await self.storage.get_entity(active.object_id)
                    old_obj_name = old_obj.canonical_name if old_obj else str(active.object_id)
                    conflict_reason = (
                        f"Contradiction with active belief: '{subj_entity.canonical_name} {canonical_pred} {old_obj_name}'. "
                        f"Proposed update to: '{obj_entity.canonical_name}' requires human authorization."
                    )
                    break

        # Step 4: Tier Decision
        if is_contradiction or confidence < 0.85:
            # TIER B: Staged in Review Queue for human operator decision
            proposal = ReviewProposal(
                proposal_type="CONTRADICTION_UPDATE" if is_contradiction else "NEW_TRIPLE",
                subject_raw=subject_raw,
                predicate_raw=predicate_raw,
                object_raw=object_raw,
                resolved_subject_id=subj_entity.id,
                resolved_predicate=canonical_pred,
                resolved_object_id=obj_entity.id,
                confidence=confidence,
                tier=ProposalTier.TIER_B,
                status=ProposalStatus.PENDING,
                conflict_reason=conflict_reason,
                conflicting_triple_id=conflicting_triple.id if conflicting_triple else None,
                source_event_id=source_event_id,
            )
            prop_id = await self.storage.stage_review_proposal(proposal)
            proposal.id = prop_id
            return proposal, None

        else:
            # TIER A: High confidence, zero contradiction -> Auto-Commit
            canonical_triple = Triple(
                subject_id=subj_entity.id,
                predicate=canonical_pred,
                object_id=obj_entity.id,
                confidence=confidence,
                source_event_ids=[source_event_id] if source_event_id else [],
            )
            triple_id = await self.storage.save_triple(canonical_triple)
            canonical_triple.id = triple_id

            # Also log proposal as AUTO_COMMITTED for auditing
            proposal = ReviewProposal(
                proposal_type="NEW_TRIPLE",
                subject_raw=subject_raw,
                predicate_raw=predicate_raw,
                object_raw=object_raw,
                resolved_subject_id=subj_entity.id,
                resolved_predicate=canonical_pred,
                resolved_object_id=obj_entity.id,
                confidence=confidence,
                tier=ProposalTier.TIER_A,
                status=ProposalStatus.AUTO_COMMITTED,
                source_event_id=source_event_id,
            )
            prop_id = await self.storage.stage_review_proposal(proposal)
            proposal.id = prop_id
            return proposal, canonical_triple
