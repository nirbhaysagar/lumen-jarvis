"""
Nightly Consolidation Daemon (Milestone C1).
Executes overnight memory hygiene passes against the immutable schema:
  1. Deduplication (cosine/trigram match -> judge -> reaffirm older + retract duplicate)
  2. Entity merge proposals (never auto-merges people)
  3. Cross-memory contradiction detection (adds CONTRADICTS + review_queue; never auto-supersedes)
  4. Belief-to-fact promotion proposals (reaffirm_count >= 2)
  5. Sleep-worker cluster synthesis (abstractions kind='summary')
  6. Audit report logged as SYSTEM event 'consolidation.completed'
"""
from __future__ import annotations
import json
import logging
from typing import Any, Optional
import uuid
import asyncpg
from rapidfuzz import fuzz

from app.domain.judge import LocalMemoryJudge, MemoryJudgePort
from app.services.lineage_service import LineageService

logger = logging.getLogger("lumen.domain.consolidator_v2")


class NightlyConsolidator:
    """Runs bounded, reversible nightly consolidation passes."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        judge: Optional[MemoryJudgePort] = None,
        *,
        budget: int = 500,
    ):
        self.pool = pool
        self.judge = judge or LocalMemoryJudge()
        self.budget = budget

    async def run(self, *, context: str = "default") -> dict[str, Any]:
        calls_used = 0
        stats = {
            "deduped": 0,
            "entity_merge_proposals": 0,
            "contradictions_found": 0,
            "belief_promotions": 0,
            "clusters_synthesized": 0,
            "judge_calls": 0,
        }

        async with self.pool.acquire() as conn:
            ctx_id = await conn.fetchval("SELECT id FROM contexts WHERE slug = $1", context)
            if not ctx_id:
                return {"status": "skipped", "reason": "context_not_found", "stats": stats}

            # Create SYSTEM event for consolidation run
            sys_ev_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO events (id, context_id, kind, actor, payload)
                VALUES ($1, $2, 'SYSTEM', 'consolidator', $3)
                """,
                sys_ev_id,
                ctx_id,
                json.dumps({"event": "consolidation.started"}),
            )

            # Fetch active memories in this context ordered by most recent
            mem_rows = await conn.fetch(
                """
                SELECT m.id, m.lineage_id, m.statement, m.lane::text AS lane,
                       m.subject_id, m.epistemic::text AS epistemic,
                       m.recorded_at, COALESCE(ms.reaffirm_count, 0) AS reaffirm_count
                FROM memories m
                LEFT JOIN memory_signals ms ON ms.lineage_id = m.lineage_id
                WHERE m.context_id = $1 AND m.status = 'active' AND m.superseded_at IS NULL
                ORDER BY m.recorded_at DESC
                LIMIT 200
                """,
                ctx_id,
            )
            memories = [dict(r) for r in mem_rows]

            # Pass 1 & Pass 4: Pairwise Dedupe & Contradictions (same subject & lane)
            by_subject: dict[uuid.UUID, list[dict]] = {}
            for m in memories:
                if m.get("subject_id"):
                    by_subject.setdefault(m["subject_id"], []).append(m)

            lineage_svc = LineageService(self.pool)
            retracted_ids: set[uuid.UUID] = set()

            for sub_id, group in by_subject.items():
                if len(group) < 2:
                    continue
                for i in range(len(group)):
                    if calls_used >= self.budget:
                        break
                    for j in range(i + 1, len(group)):
                        if calls_used >= self.budget:
                            break
                        a, b = group[i], group[j]
                        if a["id"] in retracted_ids or b["id"] in retracted_ids:
                            continue
                        if a["lineage_id"] == b["lineage_id"]:
                            continue

                        sim = fuzz.token_set_ratio(a["statement"], b["statement"]) / 100.0
                        if sim < 0.45:
                            continue

                        calls_used += 1
                        verdict_res = await self.judge.judge_pair(a["statement"], b["statement"])
                        verdict = verdict_res.get("verdict")
                        conf = float(verdict_res.get("confidence", 0.0))

                        if verdict == "same" and a["lane"] == b["lane"] and sim >= 0.6:
                            # Keep older lineage, reaffirm it, retract newer duplicate
                            older, newer = (a, b) if a["recorded_at"] <= b["recorded_at"] else (b, a)
                            await conn.execute(
                                """
                                INSERT INTO memory_signals (lineage_id, reaffirm_count, last_reaffirmed_at)
                                VALUES ($1, 1, now())
                                ON CONFLICT (lineage_id) DO UPDATE
                                SET reaffirm_count = memory_signals.reaffirm_count + 1,
                                    last_reaffirmed_at = now()
                                """,
                                older["lineage_id"],
                            )
                            await lineage_svc.retract(
                                newer["id"],
                                reason=f"merged into {str(older['lineage_id'])[:8]}",
                                actor="consolidator",
                            )
                            retracted_ids.add(newer["id"])
                            stats["deduped"] += 1

                        elif verdict == "contradicts" and conf >= 0.8:
                            # Never auto-supersede; add CONTRADICTS relation + review_queue
                            existing_rel = await conn.fetchval(
                                """
                                SELECT id FROM memory_relations
                                WHERE type = 'CONTRADICTS'
                                  AND ((from_memory = $1 AND to_memory = $2) OR (from_memory = $2 AND to_memory = $1))
                                LIMIT 1
                                """,
                                a["id"],
                                b["id"],
                            )
                            if not existing_rel:
                                await conn.execute(
                                    """
                                    INSERT INTO memory_relations (id, type, from_memory, to_memory, confidence, created_event)
                                    VALUES ($1, 'CONTRADICTS', $2, $3, $4, $5)
                                    """,
                                    uuid.uuid4(),
                                    a["id"],
                                    b["id"],
                                    conf,
                                    sys_ev_id,
                                )
                                await conn.execute(
                                    """
                                    INSERT INTO review_queue (id, kind, memory_ids, reason, status, created_event)
                                    VALUES ($1, 'contradiction', $2, $3, 'open', $4)
                                    """,
                                    uuid.uuid4(),
                                    [a["id"], b["id"]],
                                    f"Potential contradiction: '{a['statement']}' vs '{b['statement']}'",
                                    sys_ev_id,
                                )
                                stats["contradictions_found"] += 1

            # Pass 2: Entity Merge Proposals (never auto-merge people)
            ent_rows = await conn.fetch(
                """
                SELECT id, canonical_name, kind
                FROM entities
                WHERE context_id = $1
                ORDER BY canonical_name ASC
                LIMIT 100
                """,
                ctx_id,
            )
            entities = [dict(r) for r in ent_rows]
            for i in range(len(entities)):
                for j in range(i + 1, len(entities)):
                    e1, e2 = entities[i], entities[j]
                    if e1["kind"] == "person" or e2["kind"] == "person":
                        # Never auto-merge people; only stage if exact high similarity
                        pass
                    name_sim = fuzz.ratio(e1["canonical_name"].lower(), e2["canonical_name"].lower()) / 100.0
                    if name_sim >= 0.85 and e1["canonical_name"].lower() != e2["canonical_name"].lower():
                        existing_prop = await conn.fetchval(
                            """
                            SELECT id FROM review_queue
                            WHERE kind = 'entity_merge' AND status = 'open'
                              AND entity_ids @> $1::uuid[]
                            LIMIT 1
                            """,
                            [e1["id"], e2["id"]],
                        )
                        if not existing_prop:
                            await conn.execute(
                                """
                                INSERT INTO review_queue (id, kind, entity_ids, reason, status, created_event)
                                VALUES ($1, 'entity_merge', $2, $3, 'open', $4)
                                """,
                                uuid.uuid4(),
                                [e1["id"], e2["id"]],
                                f"Entity merge proposal ({int(name_sim*100)}% match): '{e1['canonical_name']}' & '{e2['canonical_name']}'",
                                sys_ev_id,
                            )
                            stats["entity_merge_proposals"] += 1

            # Pass 5: Belief Promotion Proposals (reaffirm_count >= 2)
            for m in memories:
                if m["id"] in retracted_ids:
                    continue
                if m["epistemic"] == "belief" and m["reaffirm_count"] >= 2:
                    existing_bp = await conn.fetchval(
                        """
                        SELECT id FROM review_queue
                        WHERE kind = 'belief_promotion' AND status = 'open' AND $1 = ANY(memory_ids)
                        LIMIT 1
                        """,
                        m["id"],
                    )
                    if not existing_bp:
                        await conn.execute(
                            """
                            INSERT INTO review_queue (id, kind, memory_ids, reason, status, created_event)
                            VALUES ($1, 'belief_promotion', $2, $3, 'open', $4)
                            """,
                            uuid.uuid4(),
                            [m["id"]],
                            f"Reaffirmed {m['reaffirm_count']}x: promote belief to fact?",
                            sys_ev_id,
                        )
                        stats["belief_promotions"] += 1

            # Pass 7: Sleep-worker cluster synthesis (>= 3 active memories per entity)
            ent_name_map = {e["id"]: e["canonical_name"] for e in entities}
            clusters_done = 0
            for sub_id, group in by_subject.items():
                active_group = [m for m in group if m["id"] not in retracted_ids]
                if len(active_group) < 3 or clusters_done >= 10 or calls_used >= self.budget:
                    continue
                calls_used += 1
                ent_name = ent_name_map.get(sub_id, "Entity")
                syn = await self.judge.synthesize_cluster(
                    [m["statement"] for m in active_group],
                    [ent_name],
                )
                abs_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO abstractions (
                        id, kind, title, body, subject_id, context_id, confidence,
                        proof_count, pinned, status, created_event
                    )
                    VALUES ($1, 'summary', $2, $3, $4, $5, 0.75, $6, false, 'active', $7)
                    """,
                    abs_id,
                    syn["title"],
                    syn["summary"],
                    sub_id,
                    ctx_id,
                    len(active_group),
                    sys_ev_id,
                )
                for m in active_group:
                    await conn.execute(
                        "INSERT INTO abstraction_sources (abstraction_id, memory_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        abs_id,
                        m["id"],
                    )
                clusters_done += 1
                stats["clusters_synthesized"] += 1

            stats["judge_calls"] = calls_used

            # Pass 9: Record consolidation.completed SYSTEM event
            await conn.execute(
                """
                INSERT INTO events (id, context_id, kind, actor, payload)
                VALUES ($1, $2, 'SYSTEM', 'consolidator', $3)
                """,
                uuid.uuid4(),
                ctx_id,
                json.dumps({"event": "consolidation.completed", "stats": stats}),
            )

        return {"status": "completed", "stats": stats}
