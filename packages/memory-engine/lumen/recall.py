"""Context Engine: chain-isolated Recent / Related / Trusted / Conflicts → context."""
from __future__ import annotations
from .store import Store
from .models import Memory


def assemble_context(query: str, store: Store, limit: int = 12,
                     active_chain: str | None = None) -> dict:
    # 1. in-chain hits first (isolation: active chain can't be polluted)
    hits = store.search(query, limit=limit * 2, chain_id=active_chain)
    # 2. cross-chain top-up: only memories sharing a person/thing with in-chain
    #    hits (prevents random other-workspace noise), max 2, clearly labeled
    cross_chain: list[Memory] = []
    if active_chain:
        hit_entities = {e.lower() for m in hits for e in m.entities}
        for m in store.search(query, limit=6):
            if m.chain_id != active_chain and m not in hits:
                if hit_entities & {e.lower() for e in m.entities}:
                    cross_chain.append(m)
                    if len(cross_chain) >= 2:
                        break

    def eff(m: Memory) -> float:
        return m.effective_confidence

    # Health rule: only the living answer. Superseded/archived surface solely
    # as Conflicts (what changed), never as current truth.
    live = [m for m in hits if m.status == "active"]
    # Pins first: what you pinned outranks recency.
    live.sort(key=lambda m: (m.pinned or 0, m.created_at), reverse=True)
    trusted = [m for m in live if eff(m) >= 0.8][:4]
    recent = live[:4]
    related = [m for m in live if m not in recent][:4]
    # chain context: recent workspace truth even without keyword match
    # (WHY: "change pricing?" needs goal + feedback, not just price hits)
    if active_chain:
        have = {m.id for m in hits}
        chain_recent = sorted(
            [m for m in store.get_active(chain_id=active_chain) if m.id not in have],
            key=lambda m: m.created_at, reverse=True)[:2]
        related = (related + chain_recent)[:4]

    # conflicts: real supersedes edges only, deduped
    conflicts: list[tuple[Memory, Memory]] = []
    by_id = {m.id: m for m in hits + cross_chain}
    for m in list(hits):
        for e in store.edges_for(m.id):
            if e.type != "supersedes":
                continue
            other_id = e.to_id if e.from_id == m.id else e.from_id
            other = by_id.get(other_id)
            if other is None:
                rows = store.db.execute(
                    "SELECT * FROM memories WHERE id=?", (other_id,)).fetchone()
                if rows:
                    other = store._row_to_mem(rows)
                    by_id[other_id] = other
            if other is None:
                continue
            if other.status == "superseded":
