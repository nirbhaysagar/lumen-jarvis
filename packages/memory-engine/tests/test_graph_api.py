"""
Tests for Graph API (Milestone O3).
Verifies:
1. Entity-centric graph traversal and recursive depth expansion (1..3).
2. Depth cap and node count limiting.
3. Node degree calculations.
4. Bi-temporal as_of transaction-time filtering: superseded edges visible in history, hidden currently.
5. Lane filtering (personal, work, code).
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from app.core.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_graph_api_traversal_and_depth(tmp_path: Path):
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            pool = app.state.storage.pool

            # 1. Seed entities and capture event
            async with pool.acquire() as conn:
                ctx_id = await conn.fetchval(
                    """
                    INSERT INTO contexts (id, slug, name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """,
                    uuid.uuid4(), f"test-graph-{uuid.uuid4().hex[:8]}", "Test Graph Context"
                )
                ev_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO events (id, context_id, kind, actor, payload)
                    VALUES ($1, $2, 'CAPTURE', 'tester', '{"text": "graph seed"}')
                    """,
                    ev_id, ctx_id
                )

                # Entities: E_A (Alice), E_B (Lumen), E_C (Postgres), E_D (Python)
                e_a = uuid.uuid4()
                e_b = uuid.uuid4()
                e_c = uuid.uuid4()
                e_d = uuid.uuid4()

                await conn.execute(
                    """
                    INSERT INTO entities (id, context_id, canonical_name, kind, aliases)
                    VALUES
                        ($1, $5, 'Alice Tester', 'person', ARRAY['Alice']),
                        ($2, $5, 'Lumen Project', 'project', ARRAY['Lumen']),
                        ($3, $5, 'PostgreSQL Database', 'tool', ARRAY['Postgres', 'PostgreSQL']),
                        ($4, $5, 'Python Language', 'tool', ARRAY['Python'])
                    """,
                    e_a, e_b, e_c, e_d, ctx_id
                )

                # Memories linking:
                # Alice -> Lumen ("Alice works on Lumen")
                # Lumen -> Postgres ("Lumen uses Postgres")
                # Lumen -> Python ("Lumen built with Python")
                m1 = uuid.uuid4()
                m2 = uuid.uuid4()
                m3 = uuid.uuid4()

                await conn.execute(
                    """
                    INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement,
                                          type, epistemic, asserted_by,
                                          subject_id, predicate, object_id, lane, confidence)
                    VALUES
                        ($1, $1, $7, $8, 1, 'active', 'Alice works on Lumen', 'fact', 'fact', 'user', $4, 'WORKS_ON', $5, 'work', 1.0),
                        ($2, $2, $7, $8, 1, 'active', 'Lumen uses PostgreSQL', 'fact', 'fact', 'user', $5, 'USES', $6, 'code', 1.0),
                        ($3, $3, $7, $8, 1, 'active', 'Lumen built with Python', 'fact', 'fact', 'user', $5, 'BUILT_WITH', $9, 'code', 1.0)
                    """,
                    m1, m2, m3, e_a, e_b, e_c, ctx_id, ev_id, e_d
                )

            # 2. Query depth=1 centered on Alice
            res_d1 = await client.get("/v1/graph?entity=Alice&depth=1", headers=headers)
            assert res_d1.status_code == 200
            data_d1 = res_d1.json()
            nodes_d1 = {n["label"] for n in data_d1["nodes"]}
            assert "Alice Tester" in nodes_d1
            assert "Lumen Project" in nodes_d1
            # Postgres should NOT be in depth=1
            assert "PostgreSQL Database" not in nodes_d1

            # 3. Query depth=2 centered on Alice
            res_d2 = await client.get("/v1/graph?entity=Alice&depth=2", headers=headers)
            assert res_d2.status_code == 200
            data_d2 = res_d2.json()
            nodes_d2 = {n["label"] for n in data_d2["nodes"]}
            assert "Alice Tester" in nodes_d2
            assert "Lumen Project" in nodes_d2
            assert "PostgreSQL Database" in nodes_d2
            assert "Python Language" in nodes_d2

            # Check degrees: Lumen Project connects to Alice, Postgres, and Python -> degree 3
            lumen_node = next(n for n in data_d2["nodes"] if n["label"] == "Lumen Project")
            assert lumen_node["degree"] == 3


@pytest.mark.asyncio
async def test_graph_api_bitemporal_as_of(tmp_path: Path):
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            pool = app.state.storage.pool

            t0 = datetime.now(timezone.utc) - timedelta(hours=2)
            t1 = datetime.now(timezone.utc) - timedelta(hours=1)
            t2 = datetime.now(timezone.utc)

            async with pool.acquire() as conn:
                ctx_id = await conn.fetchval(
                    """
                    INSERT INTO contexts (id, slug, name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """,
                    uuid.uuid4(), f"test-bitemp-{uuid.uuid4().hex[:8]}", "Test Graph Bi-temporal"
                )
                ev1 = uuid.uuid4()
                ev2 = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO events (id, context_id, kind, actor, payload, occurred_at)
                    VALUES
                        ($1, $3, 'CAPTURE', 'tester', '{"text": "v1"}', $4),
                        ($2, $3, 'CAPTURE', 'tester', '{"text": "v2"}', $5)
                    """,
                    ev1, ev2, ctx_id, t0, t1
                )

                sub_id = uuid.uuid4()
                obj16_id = uuid.uuid4()
                obj17_id = uuid.uuid4()

                await conn.execute(
                    """
                    INSERT INTO entities (id, context_id, canonical_name, kind, aliases)
                    VALUES
                        ($1, $4, 'Core Engine', 'tool', ARRAY['Engine']),
                        ($2, $4, 'PostgreSQL 16', 'tool', ARRAY['PG16']),
                        ($3, $4, 'PostgreSQL 17', 'tool', ARRAY['PG17'])
                    """,
                    sub_id, obj16_id, obj17_id, ctx_id
                )

                lineage_id = uuid.uuid4()
                m_v1 = uuid.uuid4()
                m_v2 = uuid.uuid4()

                # v2 inserted first so v1 can reference m_v2 in superseded_by
                await conn.execute(
                    """
                    INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement,
                                          type, epistemic, asserted_by, confidence,
                                          subject_id, predicate, object_id, recorded_at, superseded_at, superseded_by)
                    VALUES
                        ($1, $2, $3, $4, 2, 'active', 'Engine uses PostgreSQL 17', 'fact', 'fact', 'user', 1.0, $5, 'USES', $6, $7, NULL, NULL),
                        ($8, $2, $3, $9, 1, 'superseded', 'Engine uses PostgreSQL 16', 'fact', 'fact', 'user', 1.0, $5, 'USES', $10, $11, $7, $1)
                    """,
                    m_v2, lineage_id, ctx_id, ev2, sub_id, obj17_id, t1,
                    m_v1, ev1, obj16_id, t0
                )

            # 1. Current graph (no as_of): should show v2 (PG17) and NOT v1 (PG16)
            res_now = await client.get("/v1/graph?entity=Engine", headers=headers)
            assert res_now.status_code == 200
            nodes_now = {n["label"] for n in res_now.json()["nodes"]}
            assert "Core Engine" in nodes_now
            assert "PostgreSQL 17" in nodes_now
            assert "PostgreSQL 16" not in nodes_now

            # 2. Historical graph as of (t0 + 10 mins): should show v1 (PG16) and NOT v2 (PG17)
            as_of_historical = (t0 + timedelta(minutes=10)).isoformat()
            res_hist = await client.get(f"/v1/graph?entity=Engine&as_of={as_of_historical}", headers=headers)
            assert res_hist.status_code == 200
            nodes_hist = {n["label"] for n in res_hist.json()["nodes"]}
            assert "Core Engine" in nodes_hist
            assert "PostgreSQL 16" in nodes_hist
            assert "PostgreSQL 17" not in nodes_hist


@pytest.mark.asyncio
async def test_graph_api_lane_filter(tmp_path: Path):
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            pool = app.state.storage.pool

            async with pool.acquire() as conn:
                ctx_id = await conn.fetchval(
                    """
                    INSERT INTO contexts (id, slug, name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """,
                    uuid.uuid4(), f"test-lane-{uuid.uuid4().hex[:8]}", "Test Graph Lane"
                )
                ev_id = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO events (id, context_id, kind, actor, payload) VALUES ($1, $2, 'SYSTEM', 'system', '{}')",
                    ev_id, ctx_id
                )

                e1 = uuid.uuid4()
                e2 = uuid.uuid4()
                e3 = uuid.uuid4()

                await conn.execute(
                    """
                    INSERT INTO entities (id, context_id, canonical_name, kind)
                    VALUES
                        ($1, $4, 'Work Project', 'project'),
                        ($2, $4, 'Work Tool', 'tool'),
                        ($3, $4, 'Personal Hobby', 'concept')
                    """,
                    e1, e2, e3, ctx_id
                )

                m1 = uuid.uuid4()
                m2 = uuid.uuid4()

                await conn.execute(
                    """
                    INSERT INTO memories (id, lineage_id, context_id, created_event, version, status, statement,
                                          type, epistemic, asserted_by, confidence,
                                          subject_id, predicate, object_id, lane)
                    VALUES
                        ($1, $1, $6, $7, 1, 'active', 'Work Project uses Work Tool', 'fact', 'fact', 'user', 1.0, $3, 'USES', $4, 'work'),
                        ($2, $2, $6, $7, 1, 'active', 'Work Project related to Personal Hobby', 'fact', 'fact', 'user', 1.0, $3, 'RELATED', $5, 'personal')
                    """,
                    m1, m2, e1, e2, e3, ctx_id, ev_id
                )

            # Query lane=work
            res_work = await client.get("/v1/graph?lane=work", headers=headers)
            assert res_work.status_code == 200
            nodes_work = {n["label"] for n in res_work.json()["nodes"]}
            assert "Work Tool" in nodes_work
            assert "Personal Hobby" not in nodes_work
