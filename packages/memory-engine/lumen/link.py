"""FIX for flaw #3: thresholded, capped linking. No hairball."""
from __future__ import annotations
from .models import Memory, Edge, EdgeType
from .store import Store


def link(new: Memory, store: Store, candidates: list[Memory] | None = None):
    cands = candidates if candidates is not None else store.search(
        " ".join(new.entities) or new.content[:40])
    added = 0
    for c in cands:
        if c.id == new.id or added >= 8:
            break
        shared = {e.lower() for e in c.entities} & {e.lower() for e in new.entities}
        if not shared:
            continue
        conf = 0.5 + 0.1 * len(shared)
        if c.type == new.type:
            conf += 0.1
        if conf >= 0.6:
            store.add_edge(Edge(from_id=new.id, to_id=c.id,
                                type=EdgeType.RELATED_TO.value, confidence=round(conf, 2)))
            added += 1
    return added

