"""
5-Channel Hybrid Recall Adapter (Milestone M5).
Combines:
  1. Dense vector similarity (pgvector cosine distance)
  2. Lexical search (BM25 / ts_rank_cd + pg_trgm)
  3. Entity link traversal
  4. Knowledge graph expansion (1-hop relations)
  5. Temporal decay & recency weighting
Aggregated using Reciprocal Rank Fusion (RRF, k=60).
"""
from __future__ import annotations
import json
import math
import time
import uuid
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, List, Optional, Literal
import asyncpg
from app.domain.ports import EmbeddingPort

logger = logging.getLogger("lumen.memory.recall")


class RecallResult(list):
    """
    List of recalled memories that also exposes status, abstain_reason, confidence, and trace_id
    for full compatibility with both list consumers and structured RecallResponse consumers.
    """
    def __init__(
        self,
        items: list[dict[str, Any]],
        status: Literal["ok", "abstain"] = "ok",
        abstain_reason: Optional[str] = None,
        confidence: Optional[float] = None,
        trace_id: Optional[uuid.UUID] = None,
    ):
        super().__init__(items)
        self.status = status
        self.abstain_reason = abstain_reason
        self.confidence = confidence
        self.trace_id = trace_id or uuid.uuid4()


class PostgresRecallAdapter:
    def __init__(self, pool: asyncpg.Pool, embedding_adapter: Optional[EmbeddingPort] = None):
        self.pool = pool
        self.embedding = embedding_adapter

    async def recall(
        self,
        query: str,
        context_slug: str = "default",
        limit: int = 5,
        threshold: float = 0.52,
        time_travel_at: Optional[datetime] = None,
        valid_at: Optional[datetime] = None,
    ) -> RecallResult:
        """
        Executes 5-channel hybrid recall and returns ranked, citation-tagged memories.
        Supports bi-temporal queries via time_travel_at (transaction time) and valid_at (world time).
        Enforces epistemic honesty: only content channels nominate candidates; temporal decays/boosts
        only existing candidates; abstains when no verified candidates meet thresholds.
        """
        start_time = time.monotonic()
        trace_id = uuid.uuid4()
        cleaned_query = query.strip()
        if not cleaned_query:
            return RecallResult([], status="abstain", abstain_reason="no_candidates", trace_id=trace_id)

        async with self.pool.acquire() as conn:
            # 1. Resolve context_id
            ctx_row = await conn.fetchrow("SELECT id FROM contexts WHERE slug = $1", context_slug)
            if not ctx_row:
                return RecallResult([], status="abstain", abstain_reason="no_candidates", trace_id=trace_id)
            context_id = ctx_row["id"]

            channel_ranks: dict[str, list[uuid.UUID]] = defaultdict(list)
            now_dt = datetime.now(timezone.utc)

            # ── Channel 1: Dense Vector Similarity ──
            # Tightened gate: minimum cosine similarity >= threshold (default 0.35)
            if self.embedding:
                try:
                    q_vec = await self.embedding.generate_embedding(cleaned_query, prefix="search_query")
                    vec_rows = await conn.fetch(
                        """
                        WITH visible_memories AS (
                            SELECT * FROM memories
                            WHERE context_id = $2
                              AND status = 'active'
                              AND (
                                  CASE
                                      WHEN $3::timestamptz IS NOT NULL
                                          THEN recorded_at <= $3 AND (superseded_at IS NULL OR superseded_at > $3)
                                      ELSE superseded_at IS NULL
                                  END
                              )
                              AND (
                                  CASE
                                      WHEN $4::timestamptz IS NOT NULL
                                          THEN valid_from <= $4 AND (valid_to IS NULL OR valid_to > $4)
                                      ELSE TRUE
                                  END
                              )
                        )
                        SELECT id, 1 - (embedding <=> $1) AS sim
                        FROM visible_memories
                        WHERE embedding IS NOT NULL
                        ORDER BY embedding <=> $1 ASC
                        LIMIT 20
                        """,
                        list(q_vec),
                        context_id,
                        time_travel_at,
                        valid_at,
                    )
                    channel_ranks["vector"] = [r["id"] for r in vec_rows if r["sim"] is not None and r["sim"] >= threshold]
                except Exception as e:
                    logger.warning(f"Vector recall channel failed: {e}")

            # ── Channel 2: Lexical Full-Text Search (ts_rank_cd + trgm) ──
            # Tightened gate: requires tsv match or trigram similarity >= 0.3
            try:
                lex_rows = await conn.fetch(
                    """
                    WITH visible_memories AS (
                        SELECT * FROM memories
                        WHERE context_id = $2
                          AND status = 'active'
                          AND (
                              CASE
                                  WHEN $3::timestamptz IS NOT NULL
                                      THEN recorded_at <= $3 AND (superseded_at IS NULL OR superseded_at > $3)
                                  ELSE superseded_at IS NULL
                              END
                          )
                          AND (
                              CASE
                                  WHEN $4::timestamptz IS NOT NULL
                                      THEN valid_from <= $4 AND (valid_to IS NULL OR valid_to > $4)
                                  ELSE TRUE
                              END
                          )
                    )
                    SELECT id,
                           (ts_rank_cd(tsv, plainto_tsquery('english', $1)) * 0.7 +
                            similarity(statement, $1) * 0.3) AS score
                    FROM visible_memories
                    WHERE (
                        tsv @@ plainto_tsquery('english', $1)
                        OR similarity(statement, $1) >= 0.3
                    )
                    ORDER BY score DESC
                    LIMIT 20
                    """,
                    cleaned_query,
                    context_id,
                    time_travel_at,
                    valid_at,
                )
                channel_ranks["lexical"] = [r["id"] for r in lex_rows]
            except Exception as e:
                logger.warning(f"Lexical recall channel failed: {e}")

            # ── Channel 3: Entity Traversal ──
            try:
                entity_rows = await conn.fetch(
                    """
                    WITH visible_memories AS (
                        SELECT * FROM memories
                        WHERE context_id = $2
                          AND status = 'active'
                          AND (
                              CASE
                                  WHEN $3::timestamptz IS NOT NULL
                                      THEN recorded_at <= $3 AND (superseded_at IS NULL OR superseded_at > $3)
                                  ELSE superseded_at IS NULL
                              END
                          )
                          AND (
                              CASE
                                  WHEN $4::timestamptz IS NOT NULL
                                      THEN valid_from <= $4 AND (valid_to IS NULL OR valid_to > $4)
                                  ELSE TRUE
                              END
                          )
                    )
                    SELECT DISTINCT m.id
                    FROM visible_memories m
                    JOIN entities e ON (m.subject_id = e.id OR m.object_id = e.id)
                    LEFT JOIN entity_aliases a ON a.entity_id = e.id
                    WHERE (
                        lower($1) LIKE '%' || lower(e.canonical_name) || '%'
                        OR (a.alias IS NOT NULL AND lower($1) LIKE '%' || lower(a.alias) || '%')
                        OR similarity(e.canonical_name, $1) > 0.25
                    )
                    LIMIT 20
                    """,
                    cleaned_query,
                    context_id,
                    time_travel_at,
                    valid_at,
                )
                channel_ranks["entity"] = [r["id"] for r in entity_rows]
            except Exception as e:
                logger.warning(f"Entity recall channel failed: {e}")

            # Candidate nomination rule: ONLY content channels (vector ∪ lexical ∪ entity) nominate candidates
            content_candidates = set(channel_ranks.get("vector", []) + channel_ranks.get("lexical", []) + channel_ranks.get("entity", []))

            # ── Channel 4: Graph Expansion (1-hop strictly from content candidate matches) ──
            if content_candidates:
                try:
                    cand_ids = list(content_candidates)
                    graph_rows = await conn.fetch(
                        """
                        WITH visible_memories AS (
                            SELECT * FROM memories
                            WHERE context_id = $2
                              AND status = 'active'
                              AND (
                                  CASE
                                      WHEN $3::timestamptz IS NOT NULL
                                          THEN recorded_at <= $3 AND (superseded_at IS NULL OR superseded_at > $3)
                                      ELSE superseded_at IS NULL
                                  END
                              )
                              AND (
                                  CASE
                                      WHEN $4::timestamptz IS NOT NULL
                                          THEN valid_from <= $4 AND (valid_to IS NULL OR valid_to > $4)
                                      ELSE TRUE
                                  END
                              )
                        )
                        SELECT DISTINCT target.id
                        FROM memory_relations r
                        JOIN visible_memories target ON (target.id = r.to_memory OR target.id = r.from_memory)
                        WHERE (r.from_memory = ANY($1::uuid[]) OR r.to_memory = ANY($1::uuid[]))
                        LIMIT 20
                        """,
                        cand_ids,
                        context_id,
                        time_travel_at,
                        valid_at,
                    )
                    channel_ranks["graph"] = [
                        r["id"] for r in graph_rows if r["id"] not in content_candidates
                    ]
                except Exception as e:
                    logger.warning(f"Graph expansion channel failed: {e}")

            all_candidates = set(content_candidates) | set(channel_ranks.get("graph", []))

            # ── Channel 5: Temporal Decay / Recency (Boost on existing candidates ONLY) ──
            if all_candidates:
                try:
                    rec_rows = await conn.fetch(
                        """
                        WITH visible_memories AS (
                            SELECT * FROM memories
                            WHERE context_id = $1
                              AND id = ANY($2::uuid[])
                              AND status = 'active'
                              AND (
                                  CASE
                                      WHEN $3::timestamptz IS NOT NULL
                                          THEN recorded_at <= $3 AND (superseded_at IS NULL OR superseded_at > $3)
                                      ELSE superseded_at IS NULL
                                  END
                              )
                              AND (
                                  CASE
                                      WHEN $4::timestamptz IS NOT NULL
                                          THEN valid_from <= $4 AND (valid_to IS NULL OR valid_to > $4)
                                      ELSE TRUE
                                  END
                              )
                        )
                        SELECT id,
                               (importance * confidence * exp(-0.015 * EXTRACT(EPOCH FROM (now() - recorded_at)) / 86400.0)) AS score
                        FROM visible_memories
                        ORDER BY score DESC
                        LIMIT 20
                        """,
                        context_id,
                        list(all_candidates),
                        time_travel_at,
                        valid_at,
                    )
                    channel_ranks["temporal"] = [r["id"] for r in rec_rows]
                except Exception as e:
                    logger.warning(f"Temporal recall channel failed: {e}")

            # Abstention Check: If no content/graph candidates were nominated, abstain!
            if not all_candidates:
                latency_ms = int((time.monotonic() - start_time) * 1000)
                await conn.execute(
                    """
                    INSERT INTO retrieval_traces (
                        id, query, context_ids, as_of, plan, channels, fused, returned, latency_ms, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    trace_id,
                    cleaned_query,
                    [context_id],
                    time_travel_at or valid_at or now_dt,
                    json.dumps({"limit": limit, "threshold": threshold, "time_travel_at": str(time_travel_at) if time_travel_at else None, "valid_at": str(valid_at) if valid_at else None}),
                    json.dumps({k: [str(x) for x in v] for k, v in channel_ranks.items()}),
                    json.dumps([]),
                    [],
                    latency_ms,
                    now_dt,
                )
                return RecallResult([], status="abstain", abstain_reason="no_candidates", trace_id=trace_id)

            # ── Reciprocal Rank Fusion (RRF, k=60) ──
            K = 60.0
            weights = {
                "vector": 1.0,
                "lexical": 1.0,
                "entity": 0.8,
                "graph": 0.6,
                "temporal": 0.4,
            }

            rrf_scores: dict[uuid.UUID, float] = defaultdict(float)
            for ch_name, ids in channel_ranks.items():
                w = weights.get(ch_name, 1.0)
                for rank_idx, mid in enumerate(ids):
                    rrf_scores[mid] += w / (K + rank_idx + 1)

            if not rrf_scores:
                latency_ms = int((time.monotonic() - start_time) * 1000)
                await conn.execute(
                    """
                    INSERT INTO retrieval_traces (
                        id, query, context_ids, as_of, plan, channels, fused, returned, latency_ms, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    trace_id,
                    cleaned_query,
                    [context_id],
                    time_travel_at or valid_at or now_dt,
                    json.dumps({"limit": limit, "threshold": threshold}),
                    json.dumps({k: [str(x) for x in v] for k, v in channel_ranks.items()}),
                    json.dumps([]),
                    [],
                    latency_ms,
                    now_dt,
                )
                return RecallResult([], status="abstain", abstain_reason="no_candidates", trace_id=trace_id)

            # Sort top-k memory IDs by combined RRF score
            sorted_mids = sorted(rrf_scores.keys(), key=lambda mid: rrf_scores[mid], reverse=True)[:limit]

            # Fetch rich details for top memories (including bi-temporal validity)
            mem_rows = await conn.fetch(
                """
                SELECT m.id, m.lineage_id, m.version, m.type, m.epistemic, m.statement,
                       m.confidence, m.importance, m.recorded_at, m.status,
                       m.valid_from, m.valid_to,
                       sub.canonical_name AS subject_name,
                       obj.canonical_name AS object_name
                FROM memories m
                LEFT JOIN entities sub ON m.subject_id = sub.id
                LEFT JOIN entities obj ON m.object_id = obj.id
                WHERE m.id = ANY($1::uuid[])
                """,
                sorted_mids,
            )
            mem_map = {r["id"]: dict(r) for r in mem_rows}

            # Fetch associated citations joined with events and sources
            cit_rows = await conn.fetch(
                """
                SELECT c.memory_id, c.event_id, c.quote, c.verified, c.char_start, c.char_end,
                       e.occurred_at AS said_at, e.recorded_at AS captured_at,
                       s.title AS source_title, s.kind AS source_kind
                FROM citations c
                JOIN events e ON c.event_id = e.id
                LEFT JOIN sources s ON e.source_id = s.id
                WHERE c.memory_id = ANY($1::uuid[])
                """,
                sorted_mids,
            )
            cit_map: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
            for c in cit_rows:
                cit_map[c["memory_id"]].append(dict(c))

            # Assemble ranked results with citation IDs [M1], [M2]...
            results = []
            for idx, mid in enumerate(sorted_mids):
                m = mem_map.get(mid)
                if not m:
                    continue
                cits = cit_map.get(mid, [])
                primary_said_at = cits[0].get("said_at") if cits else None
                results.append({
                    "id": str(m["id"]),
                    "citation_id": f"M{idx+1}",
                    "citation_tag": f"[M{idx+1}]",
                    "lineage_id": str(m["lineage_id"]),
                    "version": m["version"],
                    "statement": m["statement"],
                    "type": str(m["type"]),
                    "epistemic": str(m["epistemic"]),
                    "confidence": float(m["confidence"]),
                    "importance": float(m["importance"]),
                    "recorded_at": m["recorded_at"].isoformat() if m["recorded_at"] else None,
                    "valid_from": m["valid_from"].isoformat() if m.get("valid_from") else None,
                    "valid_to": m["valid_to"].isoformat() if m.get("valid_to") else None,
                    "said_at": primary_said_at.isoformat() if isinstance(primary_said_at, datetime) else (str(primary_said_at) if primary_said_at else None),
                    "subject": m.get("subject_name"),
                    "object": m.get("object_name"),
                    "citations": [
                        {
                            "event_id": str(c["event_id"]),
                            "quote": c["quote"],
                            "verified": c["verified"],
                            "char_start": c["char_start"],
                            "char_end": c["char_end"],
                            "said_at": c["said_at"].isoformat() if isinstance(c.get("said_at"), datetime) else (str(c["said_at"]) if c.get("said_at") else None),
                            "captured_at": c["captured_at"].isoformat() if isinstance(c.get("captured_at"), datetime) else (str(c["captured_at"]) if c.get("captured_at") else None),
                            "source_title": c.get("source_title"),
                            "source_kind": c.get("source_kind"),
                        }
                        for c in cits
                    ],
                    "score": round(rrf_scores[mid], 6),
                })

            # Record retrieval trace in DB
            latency_ms = int((time.monotonic() - start_time) * 1000)
            returned_uuids = [uuid.UUID(r["id"]) for r in results]
            fused_list = [{"id": str(mid), "score": rrf_scores[mid]} for mid in sorted_mids]
            await conn.execute(
                """
                INSERT INTO retrieval_traces (
                    id, query, context_ids, as_of, plan, channels, fused, returned, latency_ms, created_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                trace_id,
                cleaned_query,
                [context_id],
                time_travel_at or valid_at or now_dt,
                json.dumps({"limit": limit, "threshold": threshold, "time_travel_at": str(time_travel_at) if time_travel_at else None, "valid_at": str(valid_at) if valid_at else None}),
                json.dumps({k: [str(x) for x in v] for k, v in channel_ranks.items()}),
                json.dumps(fused_list),
                returned_uuids,
                latency_ms,
                now_dt,
            )

            if results:
                max_conf = max((r["confidence"] for r in results), default=1.0)
                return RecallResult(results, status="ok", confidence=max_conf, trace_id=trace_id)
            else:
                return RecallResult([], status="abstain", abstain_reason="no_candidates", trace_id=trace_id)

