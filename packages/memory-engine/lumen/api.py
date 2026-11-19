"""v0 Context API. Stable contract for Sheldon / future integrations."""
from __future__ import annotations
from .store import Store
from .models import RawRecord, Chain
from .extract import extract
from .canonicalize import canonicalize
from .link import link
from .recall import assemble_context

_store: Store | None = None


def get_store(path: str = "lumen.db") -> Store:
    global _store
    if _store is None:
        _store = Store.file(path)
    return _store


def create_chain(chain_id: str, name: str = "", description: str = "",
                 path: str = "lumen.db") -> dict:
    store = get_store(path)
    c = store.save_chain(Chain(id=chain_id, name=name or chain_id,
                               description=description))
    return {"id": c.id, "name": c.name}


def suggest_chain(text: str, path: str = "lumen.db") -> str:
    """Universal ingestor routing: which workspace does this text belong to?
    Scores each chain by word overlap with its recent memories + chain name.
    Falls back to 'default' (the universal inbox) when nothing matches.
    """
    import re
    store = get_store(path)
    words = set(re.findall(r"[a-z0-9]{4,}", text.lower()))
    if not words:
        return "default"
    best, best_score = "default", 0
    for c in store.list_chains():
        if c.id == "default":
            continue
        score = 0
        score += len(words & set(re.findall(r"[a-z0-9]{4,}", c.id.lower()))) * 3
        for m in store.get_active(chain_id=c.id)[-15:]:
            score += len(words & set(re.findall(r"[a-z0-9]{4,}", m.content.lower())))
            score += len(words & {e.lower() for e in m.entities}) * 2
        if score > best_score:
            best, best_score = c.id, score
    return best if best_score > 0 else "default"


def move_memory(memory_id: str, chain_id: str, path: str = "lumen.db") -> dict:
    """Re-file a card (used when auto-route guesses wrong)."""
    store = get_store(path)
    store.save_chain(Chain(id=chain_id, name=chain_id))
    store.move_memory(memory_id, chain_id)
    return {"id": memory_id, "chain_id": chain_id}


def pin(memory_id: str, pinned: int = 1, path: str = "lumen.db") -> dict:
    """Pin = never archive, ranks first. Unpin = back to normal decay."""
    get_store(path).set_pinned(memory_id, pinned)
    return {"id": memory_id, "pinned": pinned}


def ingest(text: str, origin: str = "manual", trust: float = 0.5,
           chain_id: str = "auto", path: str = "lumen.db") -> list[dict]:
    if chain_id == "auto":
        chain_id = suggest_chain(text, path=path)
    store = get_store(path)
    store.save_chain(Chain(id=chain_id, name=chain_id))
    raw = store.save_raw(RawRecord(content=text, origin=origin))
    # Step 1 pipeline: LLM read first (key-ready), rules as fallback.
    # WHY try/except everything: memory must NEVER fail to save because
    # a model hiccuped. Worst case = rules cards, flagged for review.
    mems, used_llm = None, False
    try:
        from . import llm as L
        if L.provider():
            first = extract(text, source=raw.id, chain_id=chain_id)[0]
            items = L.extract_memories(text, template=first.template)
            from .models import Memory
            mems = [Memory(type=i["type"], content=i["content"],
                           entities=i["entities"] or first.entities,
                           confidence=i["confidence"], source=raw.id,
                           source_trust=trust, chain_id=chain_id,
                           template=i["template"]) for i in items]
            used_llm = True
    except Exception:
        mems = None
    if mems is None:
        mems = extract(text, source=raw.id, chain_id=chain_id)
    out = []
    for m in mems:
        if not (m.content or "").strip():
            continue  # last-line guard: empty cards never stored, whoever made them
        m.source_trust = trust
        mem, verdict = canonicalize(m, store)
        if verdict in ("new",) or verdict.startswith("supersedes"):
            link(mem, store)
        # Meaning index, best-effort: if no embedder is live this silently
        # skips and keyword search carries on. Never blocks saving.
        try:
            from . import embed as E
            if E.available():
                vec = E.embed([mem.display_content])[0]
                store.save_embedding(mem.id, vec)
        except Exception:
            pass
        out.append({"id": mem.id, "verdict": verdict, "type": mem.type,
                    "template": mem.template, "chain_id": mem.chain_id,
                    "content": mem.content, "via": "llm" if used_llm else "rules"})
    return out


def enhance(memory_id: str, enhanced_content: str,
            path: str = "lumen.db") -> dict:
    """Back-Side curation: user edit → level 0→1→2, boosts ranking."""
    get_store(path).enhance_memory(memory_id, enhanced_content)
    return {"id": memory_id, "enhanced": True}


def endorse(memory_id: str, path: str = "lumen.db") -> dict:
    """Review action 'Looks good': user confirms a low-confidence card as-is.
    Bumps curation level without changing text — clears it from Review and
    raises its ranking, same as an edit."""
    store = get_store(path)
    row = store.db.execute("SELECT content, enhanced_content FROM memories WHERE id=?",
                           (memory_id,)).fetchone()
    if row:
        store.enhance_memory(memory_id, row[1] or row[0])
    return {"id": memory_id, "endorsed": True}


def get_review(chain_id: str | None = None, path: str = "lumen.db") -> dict:
    """Gets both side-by-side ambiguous conflicts and single low-confidence cards."""
    store = get_store(path)
    res = store.get_review_items(chain_id=chain_id)
    ser = lambda m: {
        "id": m.id, "type": m.type, "template": m.template,
        "content": m.display_content, "status": m.status,
        "chain_id": m.chain_id, "confidence": m.confidence,
        "level": m.enhancement_level, "pinned": m.pinned or 0,
        "from": store.origin_label(m.source)
    }
    return {
        "conflicts": [
            {"card_a": ser(c["card_a"]), "card_b": ser(c["card_b"]), "confidence": c["confidence"]}
            for c in res["conflicts"]
        ],
        "low_confidence": [ser(m) for m in res["low_confidence"]]
    }


def resolve_conflict(winner_id: str, loser_id: str, action: str = "supersede",
                     path: str = "lumen.db") -> dict:
    """Resolves an ambiguous conflict: supersede or keep both."""
    return get_store(path).resolve_conflict(winner_id, loser_id, action=action)


