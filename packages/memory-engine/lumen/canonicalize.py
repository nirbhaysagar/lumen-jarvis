"""FIX for flaw #1/#2: explicit authority policy + deterministic canonicalize.

Old concept: 'superseded by October' with no rule for WHO decides.
New rule: score = 0.5*recency + 0.3*source_trust + 0.2*confidence.
- Auto-supersede only if margin > 0.15 AND same entity+type overlap.
- Else mark needs_review (requires user confirm) — never silently overwrite.
- Idempotent via ingest_key (normalized text hash).
"""
from __future__ import annotations
import hashlib
import re
from .models import Memory, MemoryStatus, now_iso
from .store import Store


def ingest_key(text: str, mtype: str, chain_id: str = "default") -> str:
    norm = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(f"{chain_id}:{mtype}:{norm}".encode()).hexdigest()[:16]


def authority_score(m: Memory, is_newer: bool) -> float:
    recency = 0.8 if is_newer else 0.4
    return 0.5 * recency + 0.3 * m.source_trust + 0.2 * m.confidence


def same_topic(a: Memory, b: Memory) -> bool:
    # Two memories are the same topic only if they share 2+ significant
    # words. Entity name alone doesn't count ("Orbit churn" vs "Orbit
    # pricing" are different facts about the same thing).
    wa = set(re.findall(r"[a-z]{4,}", a.content.lower()))
    wb = set(re.findall(r"[a-z]{4,}", b.content.lower()))
    return len(wa & wb) >= 2


def canonicalize(new: Memory, store: Store) -> tuple[Memory, str]:
    """Returns (memory, verdict): new | duplicate | supersedes:X | needs_review:X.

    Chain policy (CGP isolation): auto-supersede ONLY within the same chain.
    Cross-chain same-topic matches → needs_review (no silent cross-contamination).
    """
    new.ingest_key = ingest_key(new.content, new.type, new.chain_id)
    dup = store.find_by_ingest_key(new.ingest_key)
    if dup:
        return dup, "duplicate"

    # check active memories for same-topic conflicts (same chain + cross-chain)
    for entity in new.entities:
        for old in store.get_active(entity):
            if not same_topic(old, new):
                continue
            if old.chain_id != new.chain_id:
                # cross-chain: link, never auto-supersede
                store.save_memory(new)
                from .models import Edge, EdgeType
                store.add_edge(Edge(from_id=new.id, to_id=old.id,
                                    type=EdgeType.RELATED_TO.value, confidence=0.7))
                return new, f"needs_review:{old.id}:cross_chain"
            margin = authority_score(new, True) - authority_score(old, False)
            if margin > 0.15:
                new.supersedes_id = old.id
                store.save_memory(new)
                store.update_status(old.id, MemoryStatus.SUPERSEDED.value,
                                    valid_to=now_iso())
                from .models import Edge, EdgeType
                store.add_edge(Edge(from_id=new.id, to_id=old.id,
                                    type=EdgeType.SUPERSEDES.value, confidence=0.9))
                return new, f"supersedes:{old.id}"
            store.save_memory(new)
            from .models import Edge, EdgeType
            store.add_edge(Edge(from_id=new.id, to_id=old.id,
                                type=EdgeType.CONTRADICTS.value, confidence=0.8))
            return new, f"needs_review:{old.id}"

    store.save_memory(new)
    return new, "new"

