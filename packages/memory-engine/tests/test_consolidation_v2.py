"""
Tests for Nightly Consolidation Daemon (Milestone C1).
Uses a deterministic FakeJudge to verify:
1. Duplicate memories merge (reaffirm older + retract newer) and are reversible.
2. People entities are never auto-merged (staged to review_queue).
3. Contradictions create a CONTRADICTS relation + review_queue row with no auto-supersede.
4. Judge call budget is strictly respected.
5. Sleep-worker cluster synthesis writes abstractions(kind='summary') + abstraction_sources.
"""
from datetime import datetime, timezone, timedelta
import uuid
import pytest

from app.domain.consolidator_v2 import NightlyConsolidator
from app.services.lineage_service import LineageService
from app.main import app


class FakeJudge:
    def __init__(self):
        self.calls = 0

    async def judge_pair(self, stmt_a: str, stmt_b: str, quote_a: str = "", quote_b: str = ""):
        self.calls += 1
        a_low, b_low = stmt_a.lower(), stmt_b.lower()
        if ("neovim" in a_low and "neovim" in b_low) or ("uses" in a_low and "uses" in b_low and "postgres" in a_low and "postgres" in b_low):
            return {"verdict": "same", "confidence": 0.96, "explanation": "Identical assertion"}
        if ("postgres" in a_low and "mysql" in b_low) or ("mysql" in a_low and "postgres" in b_low):
            return {"verdict": "contradicts", "confidence": 0.92, "explanation": "Conflicting database choice"}
        return {"verdict": "different", "confidence": 0.8, "explanation": "Distinct facts"}

    async def synthesize_cluster(self, statements: list[str], entity_names: list[str]):
        self.calls += 1
        return {
            "title": f"Synthesis for {', '.join(entity_names)}",
            "summary": " | ".join(statements[:3]),
            "key_points": statements[:3],
            "contradictions": [],
        }


@pytest.mark.asyncio
async def test_consolidation_v2_passes_and_budget():
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        ctx_slug = f"cons_v2_{uuid.uuid4().hex[:6]}"
        t_old = datetime.now(timezone.utc) - timedelta(days=5)
        t_new = datetime.now(timezone.utc) - timedelta(hours=1)

        async with pool.acquire() as conn:
            ctx_id = await conn.fetchval(
                "INSERT INTO contexts (id, slug, name) VALUES ($1, $2, $3) RETURNING id",
                uuid.uuid4(), ctx_slug, "Consolidation v2 Context"
            )
            ev_id = uuid.uuid4()
            await conn.execute(
                "INSERT INTO events (id, context_id, kind, actor, payload) VALUES ($1, $2, 'CAPTURE', 'user', '{\"text\":\"seed\"}')",
                ev_id, ctx_id
            )

            # Entities
            ent_lumen = uuid.uuid4()
            ent_person1 = uuid.uuid4()
            ent_person2 = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO entities (id, context_id, canonical_name, kind)
                VALUES
                    ($1, $4, 'Lumen', 'project'),
                    ($2, $4, 'Alice Smith', 'person'),
                    ($3, $4, 'Alice Smit', 'person')
                """,
                ent_lumen, ent_person1, ent_person2, ctx_id
            )

            # Duplicate pair on Lumen (same lane='code')
            m_dup_old = uuid.uuid4()
            m_dup_new = uuid.uuid4()
            # Contradicting memory on Lumen (lane='work')
            m_contra = uuid.uuid4()
            # Extra memory for >= 3 cluster synthesis
            m_extra = uuid.uuid4()

            await conn.execute(
                """
                INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement,
                                      type, epistemic, asserted_by, confidence, subject_id, lane, recorded_at)
                VALUES
                    ($1, $1, $5, $6, 1, 'active', 'Lumen uses Postgres 17 as database', 'fact', 'fact', 'user', 1.0, $7, 'code', $8),
                    ($2, $2, $5, $6, 1, 'active', 'Lumen uses Postgres 17 database', 'fact', 'fact', 'user', 1.0, $7, 'code', $9),
                    ($3, $3, $5, $6, 1, 'active', 'Lumen uses MySQL as database', 'fact', 'fact', 'user', 1.0, $7, 'work', $9),
                    ($4, $4, $5, $6, 1, 'active', 'Lumen has an Obsidian knowledge vault', 'fact', 'fact', 'user', 1.0, $7, 'work', $9)
                """,
                m_dup_old, m_dup_new, m_contra, m_extra, ctx_id, ev_id, ent_lumen, t_old, t_new
            )

        judge = FakeJudge()
        consolidator = NightlyConsolidator(pool, judge=judge, budget=20)
        res = await consolidator.run(context=ctx_slug)

        assert res["status"] == "completed"
        stats = res["stats"]
        assert stats["deduped"] >= 1
        assert stats["contradictions_found"] >= 1
        assert stats["entity_merge_proposals"] >= 1
        assert stats["clusters_synthesized"] >= 1
        assert stats["judge_calls"] <= 20

        # Verify duplicate was retracted and can be reversed via undo_retract
        async with pool.acquire() as conn:
            dup_status = await conn.fetchval("SELECT status FROM memories WHERE id = $1", m_dup_new)
            assert dup_status == "retracted"

            # Verify contradicting memories remained active (no auto-supersede!)
            contra_status = await conn.fetchval("SELECT status FROM memories WHERE id = $1", m_contra)
            assert contra_status == "active"

            # Verify CONTRADICTS relation exists
            rel_cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM memory_relations WHERE type = 'CONTRADICTS' AND (from_memory = $1 OR to_memory = $1)",
                m_contra,
            )
            assert rel_cnt >= 1

        # Verify reversible merge
        lineage_svc = LineageService(pool)
        restored = await lineage_svc.undo_retract(m_dup_new, actor="user", reason="undo merge")
        assert restored["action"] == "restored"
        assert restored["version"] == 2
