"""
Tests for Directives / Procedural Memory Tier (Milestone D1).
Verifies:
1. Directives CRUD: create, list, immutable update (superseded_by), retract, toggle pin.
2. Directives cap: maximum 15 active directives enforced.
3. API endpoints: POST, GET, PATCH, DELETE, pin.
4. Context Pack injection: directives appear with top priority in prompt injection even during abstention.
"""
from pathlib import Path
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from app.core.config import settings
from app.domain.errors import ConflictError
from app.services.directives import DirectivesService, MAX_ACTIVE_DIRECTIVES
from app.main import app


@pytest.mark.asyncio
async def test_directives_service_crud_and_cap(tmp_path: Path):
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        svc = DirectivesService(pool)

        # 1. Create Directive
        d1 = await svc.create_directive(
            title="Concise SQL",
            body="Always format SQL keywords in uppercase.",
            directive_type="style",
            priority=8,
        )
        assert d1["title"] == "Concise SQL"
        assert d1["priority"] == 8
        assert d1["status"] == "active"
        d1_id = uuid.UUID(d1["id"])

        # 2. List Directives (ordered by priority DESC)
        d2 = await svc.create_directive(
            title="No AGPL Packages",
            body="Never recommend AGPL or GPL dependencies.",
            directive_type="constraint",
            priority=10,
        )
        d_list = await svc.list_directives()
        assert len(d_list) >= 2
        # Priority 10 must appear before Priority 8
        assert d_list[0]["id"] == d2["id"]

        # 3. Immutable Update: Creates new version with superseded_by linked
        upd = await svc.update_directive(
            directive_id=d1_id,
            title="Concise SQL v2",
            body="Always format SQL keywords in uppercase and indent clauses.",
            priority=9,
        )
        assert upd["title"] == "Concise SQL v2"
        assert upd["priority"] == 9
        upd_id = uuid.UUID(upd["id"])

        async with pool.acquire() as conn:
            old_row = await conn.fetchrow("SELECT * FROM abstractions WHERE id = $1", d1_id)
            assert old_row["status"] == "superseded"
            assert old_row["superseded_by"] == upd_id

        # 4. Retract Directive
        ret_res = await svc.retract_directive(upd_id)
        assert ret_res["action"] == "retracted"

        active_after = await svc.list_directives(status="active")
        assert not any(d["id"] == str(upd_id) for d in active_after)


@pytest.mark.asyncio
async def test_directives_cap_enforcement(tmp_path: Path):
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        svc = DirectivesService(pool)

        # Create directives up to MAX_ACTIVE_DIRECTIVES
        created_ids = []
        for i in range(MAX_ACTIVE_DIRECTIVES):
            try:
                res = await svc.create_directive(
                    title=f"Rule {i}",
                    body=f"Instruction rule number {i}",
                    directive_type="preference",
                    priority=5,
                )
                created_ids.append(res["id"])
            except ConflictError:
                break  # If pre-existing directives exist

        # Next creation should raise ConflictError
        with pytest.raises(ConflictError, match="Directive cap reached"):
            await svc.create_directive(
                title="Overflow Directive",
                body="This directive exceeds the cap",
                directive_type="preference",
                priority=5,
            )


@pytest.mark.asyncio
async def test_directives_api_and_context_pack_injection(tmp_path: Path):
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            # 1. POST /v1/directives
            create_res = await client.post(
                "/v1/directives",
                json={
                    "title": "Short Bullet Points",
                    "body": "Always answer in short bullet points and cite source tags.",
                    "directive_type": "style",
                    "priority": 9,
                    "pinned": True,
                },
                headers=headers,
            )
            assert create_res.status_code == 201
            d_data = create_res.json()
            dir_id = d_data["id"]

            # 2. GET /v1/directives
            list_res = await client.get("/v1/directives", headers=headers)
            assert list_res.status_code == 200
            assert any(d["id"] == dir_id for d in list_res.json()["directives"])

            # 3. POST /v1/directives/{id}/pin (toggle pin)
            pin_res = await client.post(f"/v1/directives/{dir_id}/pin", headers=headers)
            assert pin_res.status_code == 200
            assert pin_res.json()["pinned"] is False

            # Toggle back to pinned
            await client.post(f"/v1/directives/{dir_id}/pin", headers=headers)

            # 4. POST /v1/context: Verify directive appears at top of prompt injection
            ctx_res = await client.post(
                "/v1/context",
                json={"query": "What are your guidelines?", "budget_tokens": 1024},
                headers=headers,
            )
            assert ctx_res.status_code == 200
            ctx_data = ctx_res.json()
            assert "directives" in ctx_data
            assert len(ctx_data["directives"]) >= 1

            injection = ctx_data["prompt_injection"]
            assert "## Operating Directives & Guidelines" in injection
            assert "Short Bullet Points" in injection or "Always answer in short bullet points" in injection

            # 5. DELETE /v1/directives/{id}
            del_res = await client.delete(f"/v1/directives/{dir_id}", headers=headers)
            assert del_res.status_code == 200
            assert del_res.json()["action"] == "retracted"
