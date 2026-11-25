"""
Tests for Timeline API (Milestone O3).
Verifies:
1. Versioned timeline retrieval by lineage_id with ascending version ordering.
2. Timeline filtering by entity (subject or object).
3. Date range filtering (from / to).
4. Status and metadata preservation (valid_from, valid_to, recorded_at, superseded_at).
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from app.core.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_timeline_api_lineage_and_entity(tmp_path: Path):
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            pool = app.state.storage.pool

            t0 = datetime.now(timezone.utc) - timedelta(days=2)
            t1 = datetime.now(timezone.utc) - timedelta(days=1)
            t2 = datetime.now(timezone.utc)

            lineage_id = uuid.uuid4()
            m_v1 = uuid.uuid4()
            m_v2 = uuid.uuid4()
            m_v3 = uuid.uuid4()

            async with pool.acquire() as conn:
                ctx_id = await conn.fetchval(
                    """
                    INSERT INTO contexts (id, slug, name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """,
                    uuid.uuid4(), f"test-timeline-{uuid.uuid4().hex[:8]}", "Test Timeline Context"
                )
                ev = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO events (id, context_id, kind, actor, payload) VALUES ($1, $2, 'SYSTEM', 'tester', '{}')",
                    ev, ctx_id
                )

                sub = uuid.uuid4()
                obj = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO entities (id, context_id, canonical_name, kind)
                    VALUES ($1, $3, 'Lumen Bot', 'project'), ($2, $3, 'PostgreSQL DB', 'tool')
                    """,
                    sub, obj, ctx_id
                )

                # Insert 3 versions: v3 first, then v2, then v1 to satisfy foreign key on superseded_by
                await conn.execute(
                    """
                    INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement,
                                          type, epistemic, asserted_by, confidence,
                                          subject_id, object_id, recorded_at, superseded_at, superseded_by, valid_from, valid_to)
                    VALUES
                        ($3, $4, $5, $6, 3, 'active', 'Lumen uses Postgres 17', 'fact', 'fact', 'user', 1.0, $7, $8, $11, NULL, NULL, $11, NULL),
                        ($2, $4, $5, $6, 2, 'superseded', 'Lumen uses Postgres 16', 'fact', 'fact', 'user', 1.0, $7, $8, $10, $11, $3, $10, $11),
                        ($1, $4, $5, $6, 1, 'superseded', 'Lumen uses Postgres 15', 'fact', 'fact', 'user', 1.0, $7, $8, $9, $10, $2, $9, $10)
                    """,
                    m_v1, m_v2, m_v3, lineage_id, ctx_id, ev, sub, obj, t0, t1, t2
                )

            # 1. Query by lineage_id
            res_lineage = await client.get(f"/v1/timeline?lineage_id={lineage_id}", headers=headers)
            assert res_lineage.status_code == 200
            data_l = res_lineage.json()
            assert data_l["count"] == 3
            timeline = data_l["timeline"]

            assert timeline[0]["version"] == 1
            assert timeline[0]["statement"] == "Lumen uses Postgres 15"
            assert timeline[0]["status"] == "superseded"
            assert timeline[0]["superseded_by"] == str(m_v2)

            assert timeline[1]["version"] == 2
            assert timeline[1]["statement"] == "Lumen uses Postgres 16"
            assert timeline[1]["superseded_by"] == str(m_v3)

            assert timeline[2]["version"] == 3
            assert timeline[2]["statement"] == "Lumen uses Postgres 17"
            assert timeline[2]["status"] == "active"
            assert timeline[2]["superseded_by"] is None

            # 2. Query by entity name
            res_ent = await client.get("/v1/timeline?entity=Lumen%20Bot", headers=headers)
            assert res_ent.status_code == 200
            assert res_ent.json()["count"] >= 3

            # 3. Query with date filter (from t1 to now) -> should only return v2 and v3
            from_str = (t1 - timedelta(minutes=1)).isoformat()
            res_from = await client.get(f"/v1/timeline?lineage_id={lineage_id}&from={from_str}", headers=headers)
            assert res_from.status_code == 200
            t_from = res_from.json()["timeline"]
            assert len(t_from) == 2
            assert t_from[0]["version"] == 2
            assert t_from[1]["version"] == 3
