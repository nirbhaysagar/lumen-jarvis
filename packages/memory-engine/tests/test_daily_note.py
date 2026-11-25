"""
Tests for Daily Notes and Morning Brief (Milestone O4).
Verifies:
1. DailyNoteGenerator compiles Captured, Changed (with diffs), Retracted, Open Reviews, and Goals.
2. Executive morning brief generation enforces strict citation contract (every bullet cites a [[note]]).
3. Lines without citations are dropped.
4. Output file is safely written to vault/daily/<YYYY-MM-DD>.md with valid YAML frontmatter.
"""
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import re
import uuid
import pytest
import yaml

from app.core.config import settings
from app.vault.daily import DailyNoteGenerator, format_memory_wikilink
from app.main import app


@pytest.mark.asyncio
async def test_daily_note_generation(tmp_path: Path):
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir(parents=True, exist_ok=True)

        target_date = date(2026, 11, 25)
        rec_time = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)

        # 1. Seed database with today's memories
        async with pool.acquire() as conn:
            ctx_id = await conn.fetchval(
                """
                INSERT INTO contexts (id, slug, name)
                VALUES ($1, $2, $3)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                uuid.uuid4(), f"test-daily-{uuid.uuid4().hex[:8]}", "Daily Context"
            )
            ev = uuid.uuid4()
            await conn.execute(
                "INSERT INTO events (id, context_id, kind, actor, payload) VALUES ($1, $2, 'SYSTEM', 'tester', '{}')",
                ev, ctx_id
            )

            # Memory 1: Captured (version 1)
            m1_lid = uuid.uuid4()
            m1_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement, type, epistemic, asserted_by, confidence, recorded_at)
                VALUES ($1, $2, $3, $4, 1, 'active', 'Alice joined Project Alpha', 'fact', 'fact', 'user', 1.0, $5)
                """,
                m1_id, m1_lid, ctx_id, ev, rec_time
            )

            # Memory 2: Changed (v1 was Postgres 16, v2 is Postgres 17)
            # Insert v2 first, then v1 with superseded_by = v2
            m2_lid = uuid.uuid4()
            m2_v1 = uuid.uuid4()
            m2_v2 = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement, type, epistemic, asserted_by, confidence, recorded_at)
                VALUES ($1, $2, $3, $4, 2, 'active', 'Project Alpha uses Postgres 17', 'decision', 'decision', 'user', 1.0, $5)
                """,
                m2_v2, m2_lid, ctx_id, ev, rec_time
            )
            await conn.execute(
                """
                INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement, type, epistemic, asserted_by, confidence, recorded_at, superseded_at, superseded_by)
                VALUES ($1, $3, $4, $5, 1, 'superseded', 'Project Alpha uses Postgres 16', 'decision', 'decision', 'user', 1.0, $6, $7, $2)
                """,
                m2_v1, m2_v2, m2_lid, ctx_id, ev, rec_time - timedelta(days=5), rec_time
            )

            # Memory 3: Retracted
            m3_lid = uuid.uuid4()
            m3_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement, type, epistemic, asserted_by, confidence, recorded_at, superseded_at)
                VALUES ($1, $2, $3, $4, 1, 'retracted', 'Deprecated temporary server IP', 'fact', 'belief', 'user', 1.0, $5, $5)
                """,
                m3_id, m3_lid, ctx_id, ev, rec_time
            )

            # Memory 4: Active Goal
            m4_lid = uuid.uuid4()
            m4_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement, type, epistemic, asserted_by, confidence, recorded_at)
                VALUES ($1, $2, $3, $4, 1, 'active', 'Deploy Phase 2.7 to production', 'goal', 'decision', 'user', 1.0, $5)
                """,
                m4_id, m4_lid, ctx_id, ev, rec_time
            )

            # Open Review item
            await conn.execute(
                """
                INSERT INTO review_queue (id, created_event, memory_ids, kind, reason, status)
                VALUES ($1, $2, $3, 'contradiction', 'Bob states Alpha uses MySQL', 'open')
                """,
                uuid.uuid4(), ev, [m2_v2]
            )

        # 2. Run DailyNoteGenerator
        generator = DailyNoteGenerator(pool, vault_dir=vault_dir)
        res = await generator.generate(target_date=target_date)

        assert res["date"] == "2026-11-25"
        daily_file = Path(res["path"])
        assert daily_file.exists()

        content = daily_file.read_text(encoding="utf-8")
        assert content.startswith("---")

        # 3. Check Frontmatter
        parts = content.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["date"] == "2026-11-25"
        assert fm["captured_count"] >= 1
        assert fm["changed_count"] >= 1
        assert fm["retracted_count"] >= 1

        # 4. Check Markdown Sections
        assert "## Executive Brief" in content
        assert "## Captured" in content
        assert "## Changed" in content
        assert "## Retracted" in content
        assert "## Open Reviews" in content
        assert "## Active Goals & Action Items" in content

        # 5. Check Citation Rule: Every bullet in Executive Brief MUST cite a [[...]] wikilink
        brief_bullets = res["brief_bullets"]
        assert len(brief_bullets) > 0
        for b in brief_bullets:
            assert re.search(r"\[\[[^\]]+\]\]", b), f"Brief bullet '{b}' violated the citation contract!"

        # 6. Check Diffs in Changed section
        assert "Project Alpha uses Postgres 17" in content
        assert "Project Alpha uses Postgres 16" in content
        assert "Deprecated temporary server IP" in content
        assert "Bob states Alpha uses MySQL" in content
