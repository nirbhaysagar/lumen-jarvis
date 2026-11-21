"""
Context-aware retrieval engine.

Builds a structured context payload hierarchically instead of a flat top-k chunk dump:

  1. Parse user query intent using LLM.
  2. Vector search → relevant concepts (pgvector cosine distance).
  3. For each concept → linked memories (ordered by confidence × importance).
  4. For each memory → extract primitives (decisions, facts, goals).
  5. Include recent memories (last 7 days), high-confidence facts.
  6. Flag contradictions or low-confidence memories.
  7. Assemble structured context payload.

Output dict: {"concepts": [...], "memories": [...], "primitives": [...],
              "contradictions": [...], "timeline": [...], "query_intent": str}
"""

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.embedding import aembed_text
from app.services.llm import aextract_json

settings = get_settings()

_MAX_CONCEPTS = settings.max_retrieval_concepts   # 15
_MAX_MEMORIES = settings.max_retrieval_memories    # 20
_RECENT_DAYS = 7
_DECAY_LAMBDA = 0.015  # Half-life of ~46 days


_INTENT_SYSTEM = """You are a query-intent parser for a personal memory engine.

Given a user's question, extract the core intent. Return a JSON object with:

- "intent" (string): A short label — one of "recall", "search", "analysis", "decision_review", "timeline", "general".
- "key_terms" (array of strings): 1-5 key terms or concepts the user is asking about.
- "time_filter" (string | null): "recent" (last week), "month" (last month), "all" (no time filter), or null if unclear.
- "focus" (string): What the user is primarily asking about — "facts", "decisions", "goals", "assumptions", "constraints", "action_items", "questions", "everything", or a custom string.

Keep the response concise. Return valid JSON only."""


async def build_context(
    session: AsyncSession,
    query: str,
    collection_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Assemble a full structured context payload for the given user query.

    When `collection_id` is provided, retrieval is scoped to memories in that
    collection.
    """
    # 1. Parse query intent.
    intent_data = await _parse_query_intent(query)
    key_terms: list[str] = intent_data.get("key_terms", [])
    focus: str = intent_data.get("focus", "everything")
    time_filter: str | None = intent_data.get("time_filter")

    # 2. Vector search for relevant concepts.
    query_embedding = await aembed_text(query)
    relevant_concepts = await _find_relevant_concepts(session, query_embedding, key_terms)

    # 3. Direct Hybrid RRF search on memories (combining vector distance + full-text search)
    hybrid_memories_ranked = await _hybrid_search_memories(session, query, query_embedding, collection_id)

    # 4. For each concept, find linked memories.
    concept_memory_map: dict[str, list[dict]] = {}
    seen_memory_ids: set[uuid.UUID] = set()
    all_memory_ids: list[uuid.UUID] = []

    # Add hybrid search matches first
    for mem_id, rrf_score in hybrid_memories_ranked:
        seen_memory_ids.add(mem_id)
        all_memory_ids.append(mem_id)

    for concept in relevant_concepts:
        cid = concept["id"]
        memories = await _find_memories_for_concept(session, cid, collection_id)
        for mem in memories:
            mid_uuid = uuid.UUID(mem["id"])
            if mid_uuid not in seen_memory_ids:
                seen_memory_ids.add(mid_uuid)
                all_memory_ids.append(mid_uuid)
        if memories:
            concept_memory_map[concept["name"]] = memories

    # 5. Include recent memories (last 7 days) if time_filter matches
    if time_filter in ("recent", None):
        recent_memories = await _find_recent_memories(session, _RECENT_DAYS, collection_id)
        for rm in recent_memories:
            rid = uuid.UUID(rm["id"])
            if rid not in seen_memory_ids:
                seen_memory_ids.add(rid)
                all_memory_ids.append(rid)

    # 6. Load full memory records and apply Ebbinghaus Decay & Reinforcement Scoring
    all_memories = await _load_memories_by_ids(session, all_memory_ids)
    rrf_map = {mid: score for mid, score in hybrid_memories_ranked}

    for mem in all_memories:
        mid_uuid = uuid.UUID(mem["id"])
        rrf = rrf_map.get(mid_uuid, 0.01)
        mem["retrieval_score"] = _compute_memory_score(mem, rrf)

    # Sort by retrieval score descending
    all_memories.sort(key=lambda m: m.get("retrieval_score", 0.0), reverse=True)
    all_memories = all_memories[:_MAX_MEMORIES]

    # 7. Extract primitives across all 8 taxonomy types
    primitives = await _find_primitives_for_memories(
        session, [uuid.UUID(m["id"]) for m in all_memories], focus
    )

    # 8. Find contradictions and low-confidence flags.
    contradictions = await _find_contradictions(
        session, [uuid.UUID(m["id"]) for m in all_memories]
    )

    # 9. Build timeline of relevant events.
    timeline = await _build_timeline(session, all_memories, time_filter)

    # 10. Inject active user directives (Procedural / Working Memory tier)
    directives = await _load_active_directives(session)

    # 11. Trigger retrieval reinforcement asynchronously (fire-and-forget)
    if all_memories:
        try:
            from app.workers.tasks import reinforce_memory_access
            memory_id_strs = [m["id"] for m in all_memories[:10]]
            reinforce_memory_access.delay(memory_id_strs)
        except Exception:
            pass  # Never block retrieval on reinforcement

    return {
        "concepts": relevant_concepts,
        "memories": all_memories,
        "primitives": primitives,
        "contradictions": contradictions,
        "timeline": timeline,
        "query_intent": intent_data,
        "directives": directives,
    }


async def _load_active_directives(session: AsyncSession) -> list[dict[str, Any]]:
    """Load active user directives ordered by priority (highest first)."""
    try:
        rows = await session.execute(text("""
            SELECT directive_type, content, priority, confidence
            FROM user_directives
            WHERE is_active = TRUE
            ORDER BY priority DESC, confidence DESC
            LIMIT 20
        """))
        return [
            {
                "type": row[0],
                "content": row[1],
                "priority": row[2],
                "confidence": float(row[3]) if row[3] else 1.0,
            }
            for row in rows.fetchall()
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Query intent parsing
# ---------------------------------------------------------------------------

async def _parse_query_intent(query: str) -> dict[str, Any]:
    """Use the LLM to determine what the user is really asking."""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _INTENT_SYSTEM},
        {"role": "user", "content": query},
    ]

    try:
        result = await aextract_json(messages, temperature=0.1)
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    # Fallback: basic keyword extraction.
    return {
        "intent": "general",
        "key_terms": query.split()[:5],
        "time_filter": None,
        "focus": "everything",
    }


# ---------------------------------------------------------------------------
# Vector search for concepts
# ---------------------------------------------------------------------------

async def _find_relevant_concepts(
    session: AsyncSession,
    query_embedding: list[float],
    key_terms: list[str],
) -> list[dict[str, Any]]:
    """Vector-search concepts table for the closest matches."""
    query = text("""
        SELECT
            id,
            name,
            aliases,
            description,
            reference_count,
            1.0 - (embedding <=> CAST(:emb AS vector)) AS similarity
        FROM concepts
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:emb AS vector)
        LIMIT :limit
    """)

    result = await session.execute(query, {"emb": query_embedding, "limit": _MAX_CONCEPTS})
    rows = result.fetchall()

    concepts: list[dict[str, Any]] = []
    for row in rows:
        concepts.append({
            "id": str(row[0]),
            "name": row[1],
            "aliases": row[2],
            "description": row[3],
            "reference_count": row[4],
            "similarity": round(float(row[5]), 4),
        })

    # If key terms were specified, also search by name/alias match.
    if key_terms:
        for term in key_terms:
            # Skip if already covered by vector search.
            if any(term.lower() in (c.get("name", "") + " " + " ".join(c.get("aliases") or [])).lower()
                   for c in concepts):
                continue
            name_query = await session.execute(
                text("""
                    SELECT id, name, aliases, description, reference_count
                    FROM concepts
                    WHERE LOWER(name) LIKE :term
                       OR EXISTS (SELECT 1 FROM unnest(aliases) alias WHERE LOWER(alias) LIKE :term)
                    LIMIT 3
                """),
                {"term": f"%{term.lower()}%"},
            )
            for row in name_query.fetchall():
                concepts.append({
                    "id": str(row[0]),
                    "name": row[1],
                    "aliases": row[2],
                    "description": row[3],
                    "reference_count": row[4],
                    "similarity": 0.6,  # Keyword match, lower confidence.
                })

    return concepts[:_MAX_CONCEPTS]


# ---------------------------------------------------------------------------
# Direct Hybrid RRF Search on Memories
# ---------------------------------------------------------------------------

async def _hybrid_search_memories(
    session: AsyncSession,
    query_text: str,
    query_embedding: list[float],
    collection_id: uuid.UUID | None = None,
) -> list[tuple[uuid.UUID, float]]:
    """Perform hybrid Reciprocal Rank Fusion search over the memories table.

    Combines dense pgvector cosine distance ranking with sparse text ranking.
    Returns a list of (memory_id, rrf_score) tuples sorted by RRF score descending.
    """
    scope_clause = "AND collection_id = CAST(:collection_id AS uuid)" if collection_id else ""
    scope_params = {"collection_id": str(collection_id)} if collection_id else {}

    # 1. Vector Search
    vec_query = text(f"""
        SELECT id
        FROM memories
        WHERE is_active = TRUE
          AND embedding IS NOT NULL
          {scope_clause}
        ORDER BY embedding <=> CAST(:emb AS vector)
        LIMIT 30
    """)
    vec_res = await session.execute(vec_query, {"emb": query_embedding, **scope_params})
    vec_ids = [row[0] for row in vec_res.fetchall()]

    # 2. Text Search
    clean_text = " ".join([w for w in query_text.split() if len(w) > 2])
    text_query = text(f"""
        SELECT id
        FROM memories
        WHERE is_active = TRUE
          {scope_clause}
          AND (
            title ILIKE :pattern
            OR raw_content ILIKE :pattern
            OR to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(raw_content, ''))
               @@ plainto_tsquery('english', :q)
          )
        LIMIT 30
    """)
    text_res = await session.execute(
        text_query,
        {"pattern": f"%{query_text[:100]}%", "q": clean_text or query_text, **scope_params}
    )
    text_ids = [row[0] for row in text_res.fetchall()]

    # 3. Reciprocal Rank Fusion (RRF constant k=60)
    K = 60.0
    rrf_scores: dict[uuid.UUID, float] = {}

    for rank, mid in enumerate(vec_ids):
        rrf_scores[mid] = rrf_scores.get(mid, 0.0) + (1.0 / (K + rank + 1))

    for rank, mid in enumerate(text_ids):
        rrf_scores[mid] = rrf_scores.get(mid, 0.0) + (1.0 / (K + rank + 1))

    ranked = sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)
    return ranked[:30]


def _compute_memory_score(memory: dict[str, Any], rrf_score: float) -> float:
    """Calculate the final retrieval score incorporating Ebbinghaus decay and
    retrieval reinforcement.

    Retention decays with age (created_at) but is refreshed when a memory is
    accessed recently, and repeated access provides a diminishing-returns boost.
    """
    conf = memory.get("confidence", 1.0)
    imp = memory.get("importance", 0.5)

    now = datetime.now(timezone.utc)

    # Base decay from creation date (Ebbinghaus forgetting curve).
    decay_factor = 1.0
    created_at_str = memory.get("created_at")
    if created_at_str:
        try:
            created_dt = datetime.fromisoformat(created_at_str)
            delta_days = max(0.0, (now - created_dt).total_seconds() / 86400.0)
            decay_factor = math.exp(-_DECAY_LAMBDA * delta_days)
        except Exception:
            decay_factor = 1.0

    # Recency of last access refreshes retention (spacing effect).
    access_count = memory.get("access_count", 0) or 0
    recency_factor = 0.0
    last_accessed_str = memory.get("last_accessed_at")
    if last_accessed_str:
        try:
            last_dt = datetime.fromisoformat(last_accessed_str)
            access_age_days = max(0.0, (now - last_dt).total_seconds() / 86400.0)
            recency_factor = math.exp(-_DECAY_LAMBDA * access_age_days)
        except Exception:
            recency_factor = 0.0

    retention = max(decay_factor, recency_factor)

    # Diminishing-returns reinforcement from repeated access.
    reinforcement = 1.0 + min(0.5, 0.1 * math.log1p(access_count))

    cognitive_weight = (0.5 * conf + 0.5 * max(0.1, imp))
    temporal_multiplier = 0.4 + (0.6 * retention)

    return round(rrf_score * cognitive_weight * temporal_multiplier * reinforcement, 6)


# ---------------------------------------------------------------------------
# Memory lookups
# ---------------------------------------------------------------------------

async def _find_memories_for_concept(
    session: AsyncSession,
    concept_id: str,
    collection_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Find memories linked to a concept via memory_concepts, ordered by relevance."""
    scope_clause = "AND m.collection_id = CAST(:collection_id AS uuid)" if collection_id else ""
    scope_params = {"collection_id": str(collection_id)} if collection_id else {}

    query = text(f"""
        SELECT
            m.id, m.title, m.summary, m.raw_content,
            m.confidence, m.importance, m.created_at,
            mc.relevance AS concept_relevance
        FROM memories m
        JOIN memory_concepts mc ON mc.memory_id = m.id
        WHERE mc.concept_id = CAST(:concept_id AS uuid)
          AND m.is_active = TRUE
          {scope_clause}
        ORDER BY (m.confidence * m.importance * mc.relevance) DESC
        LIMIT 10
    """)

    result = await session.execute(
        query,
        {"concept_id": concept_id, **scope_params},
    )
    rows = result.fetchall()

    memories: list[dict[str, Any]] = []
    for row in rows:
        memories.append({
            "id": str(row[0]),
            "title": row[1],
            "summary": row[2],
            "content": (row[3] or "")[:2000],
            "confidence": float(row[4]) if row[4] else 0.0,
            "importance": float(row[5]) if row[5] else 0.0,
            "created_at": row[6].isoformat() if row[6] else None,
            "concept_relevance": float(row[7]) if row[7] else 0.5,
        })

    return memories


async def _load_memories_by_ids(
    session: AsyncSession,
    memory_ids: list[uuid.UUID],
) -> list[dict[str, Any]]:
    """Bulk-load memory metadata by IDs."""
    if not memory_ids:
        return []

    id_strs = [str(mid) for mid in memory_ids]
    query = text("""
        SELECT id, title, summary, raw_content, confidence, importance, created_at, source_type,
               access_count, last_accessed_at
        FROM memories
        WHERE id = ANY(CAST(:ids AS uuid[]))
          AND is_active = TRUE
    """)

    result = await session.execute(query, {"ids": id_strs})
    rows = result.fetchall()

    memories: list[dict[str, Any]] = []
    for row in rows:
        memories.append({
            "id": str(row[0]),
            "title": row[1],
            "summary": row[2],
            "content": (row[3] or "")[:2000],
            "confidence": float(row[4]) if row[4] else 0.0,
            "importance": float(row[5]) if row[5] else 0.0,
            "created_at": row[6].isoformat() if row[6] else None,
            "source_type": row[7],
            "access_count": int(row[8]) if row[8] else 0,
            "last_accessed_at": row[9].isoformat() if row[9] else None,
        })

    return memories


async def _find_recent_memories(
    session: AsyncSession,
    days: int,
    collection_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """Fetch memories created within the last N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    scope_clause = "AND collection_id = CAST(:collection_id AS uuid)" if collection_id else ""
    scope_params = {"collection_id": str(collection_id)} if collection_id else {}

    query = text(f"""
        SELECT id, title, summary, raw_content, confidence, importance, created_at, source_type
        FROM memories
        WHERE created_at >= :since
          AND is_active = TRUE
          {scope_clause}
        ORDER BY importance DESC, confidence DESC
        LIMIT :limit
    """)

    result = await session.execute(
        query,
        {"since": since, "limit": 20, **scope_params},
    )
    rows = result.fetchall()

    memories: list[dict[str, Any]] = []
    for row in rows:
        memories.append({
            "id": str(row[0]),
            "title": row[1],
            "summary": row[2],
            "content": (row[3] or "")[:2000],
            "confidence": float(row[4]) if row[4] else 0.0,
            "importance": float(row[5]) if row[5] else 0.0,
            "created_at": row[6].isoformat() if row[6] else None,
            "source_type": row[7],
        })

    return memories


# ---------------------------------------------------------------------------
# Primitive extraction from memories
# ---------------------------------------------------------------------------

async def _find_primitives_for_memories(
    session: AsyncSession,
    memory_ids: list[uuid.UUID],
    focus: str,
) -> list[dict[str, Any]]:
    """Load primitives linked to the given memories, filtered by focus if applicable."""
    if not memory_ids:
        return []

    id_strs = [str(mid) for mid in memory_ids]

    # Build type filter based on focus across all 8 taxonomy primitives.
    type_clause = ""
    params: dict[str, Any] = {"ids": id_strs}

    match focus:
        case "facts":
            type_clause = "AND p.primitive_type = 'fact'"
        case "decisions":
            type_clause = "AND p.primitive_type = 'decision'"
        case "goals":
            type_clause = "AND p.primitive_type = 'goal'"
        case "assumptions":
            type_clause = "AND p.primitive_type = 'assumption'"
        case "constraints":
            type_clause = "AND p.primitive_type = 'constraint'"
        case "action_items":
            type_clause = "AND p.primitive_type = 'action_item'"
        case "questions":
            type_clause = "AND p.primitive_type = 'question'"
        case "insights":
            type_clause = "AND p.primitive_type = 'insight'"

    query = text(f"""
        SELECT
            p.id, p.memory_id, p.primitive_type, p.title, p.content,
            p.confidence, p.metadata
        FROM primitives p
        WHERE p.memory_id = ANY(CAST(:ids AS uuid[]))
          AND p.is_active = TRUE
          {type_clause}
        ORDER BY p.confidence DESC
        LIMIT 60
    """)

    result = await session.execute(query, params)
    rows = result.fetchall()

    primitives: list[dict[str, Any]] = []
    for row in rows:
        primitives.append({
            "id": str(row[0]),
            "memory_id": str(row[1]),
            "primitive_type": row[2],
            "title": row[3],
            "content": row[4],
            "confidence": float(row[5]) if row[5] else 1.0,
            "metadata": row[6] if row[6] else {},
            "low_confidence": (float(row[5]) or 1.0) < 0.7 if row[5] else False,
        })

    return primitives


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------

async def _find_contradictions(
    session: AsyncSession,
    memory_ids: list[uuid.UUID],
) -> list[dict[str, Any]]:
    """Find relationship rows of type CONTRADICTS among the given memories."""
    if not memory_ids:
        return []

    id_strs = [str(mid) for mid in memory_ids]

    query = text("""
        SELECT
            r.id, r.source_id, r.target_id, r.confidence, r.metadata,
            m_src.title AS source_title,
            m_tgt.title AS target_title
        FROM relationships r
        JOIN memories m_src ON m_src.id = r.source_id
        JOIN memories m_tgt ON m_tgt.id = r.target_id
        WHERE r.relationship_type = 'contradicts'
          AND (r.source_id = ANY(CAST(:ids AS uuid[]))
               OR r.target_id = ANY(CAST(:ids AS uuid[])))
    """)

    result = await session.execute(query, {"ids": id_strs})
    rows = result.fetchall()

    contradictions: list[dict[str, Any]] = []
    for row in rows:
        contradictions.append({
            "id": str(row[0]),
            "source_id": str(row[1]),
            "target_id": str(row[2]),
            "confidence": float(row[3]) if row[3] else 0.0,
            "explanation": (row[4] or {}).get("explanation", "") if row[4] else "",
            "source_title": row[5],
            "target_title": row[6],
        })

    return contradictions


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

async def _build_timeline(
    session: AsyncSession,
    memories: list[dict[str, Any]],
    time_filter: str | None,
) -> list[dict[str, Any]]:
    """Build a chronological timeline from decision and goal primitives."""
    if not memories:
        return []

    mem_ids = [m["id"] for m in memories]

    query = text("""
        SELECT
            p.id, p.memory_id, p.primitive_type, p.title, p.content,
            p.confidence, p.metadata, p.created_at
        FROM primitives p
        WHERE p.memory_id = ANY(CAST(:ids AS uuid[]))
          AND p.primitive_type IN ('decision', 'goal')
          AND p.is_active = TRUE
          AND (p.metadata->>'date' IS NOT NULL OR p.metadata->>'deadline' IS NOT NULL)
        ORDER BY p.created_at DESC
        LIMIT 30
    """)

    result = await session.execute(query, {"ids": mem_ids})
    rows = result.fetchall()

    timeline: list[dict[str, Any]] = []
    for row in rows:
        meta = row[6] or {}
        date_val = meta.get("date") or meta.get("deadline")

        timeline.append({
            "id": str(row[0]),
            "memory_id": str(row[1]),
            "type": row[2],
            "title": row[3],
            "content": row[4],
            "date": date_val,
            "confidence": float(row[5]) if row[5] else 0.5,
        })

    # Sort by date if available, else preserve order.
    timeline.sort(key=lambda t: t.get("date") or "", reverse=True)
    return timeline
