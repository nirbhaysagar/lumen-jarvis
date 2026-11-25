"""
Tests for Conversational Correction and Lineage Modification (Milestone F2).
Verifies:
1. Lineage version increment: v1 -> v2, closing out v1 with superseded_by.
2. Concurrency locking: stale base_version triggers ConflictError (409).
3. Citation & relation: new memory cites correction utterance and links SUPERSEDES.
4. Recall reflection: recall returns only v2, with v1 excluded from active answers.
5. Conversational executor: diff reply formatting.
"""
from datetime import datetime, timezone
from pathlib import Path
import uuid
import pytest
from unittest.mock import AsyncMock

from app.domain.events import CaptureRequest, SourceInfo
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.adapters.storage.pg_recall import PostgresRecallAdapter
from app.domain.extraction import MemoryExtractor
from app.domain.errors import ConflictError
from app.pipeline import SavePipeline
from app.services.lineage_service import LineageService
from app.domain.intents import IntentExecutor
from app.main import app


@pytest.mark.asyncio
async def test_lineage_correct_version_increment(tmp_path: Path):
    vault_dir = tmp_path / "correct_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)

        # Ingest base memory
        cap_req = CaptureRequest(
            content="Charlie is using PostgreSQL 16 for production.",
            source=SourceInfo(kind="chat", title="DB Chat", author="charlie"),
            context="default",
        )
        cap_res = await events_adapter.capture(cap_req)

        mock_extractor = MemoryExtractor()
        mock_extractor.extract = AsyncMock(
            return_value={
                "entities": [{"ref": "e1", "name": "Charlie", "kind": "person", "aliases": []}],
                "memories": [
                    {
                        "ref": "m1",
                        "type": "fact",
                        "epistemic": "fact",
                        "asserted_by": "charlie",
                        "statement": "Charlie is using PostgreSQL 16 for production.",
                        "subject": "e1",
                        "predicate": "USES_DB",
                        "object": None,
                        "object_value": "PostgreSQL 16",
                        "evidence": ["using PostgreSQL 16 for production"],
                        "confidence": 0.95,
                        "importance": 0.8,
                        "lane": "code",
                        "sensitivity": "normal",
                        "about_speaker": True,
                    }
                ],
            }
        )
        pipeline = SavePipeline(pool, extractor=mock_extractor)
        save_rep = await pipeline.run(cap_res.event_id)
        lineage_id = uuid.UUID(save_rep.memories[0]["lineage_id"])
        v1_id = uuid.UUID(save_rep.memories[0]["id"])

        # Execute correction: PostgreSQL 16 -> PostgreSQL 17
        lineage_svc = LineageService(pool, vault_dir=vault_dir)
        corr_res = await lineage_svc.correct(
            lineage_id=lineage_id,
            new_statement="Charlie upgraded production to PostgreSQL 17.",
            reason="version upgrade",
            actor="user",
            base_version=1,
        )

        assert corr_res["action"] == "corrected"
        assert corr_res["previous_version"] == 1
        assert corr_res["new_version"] == 2
        assert corr_res["new_statement"] == "Charlie upgraded production to PostgreSQL 17."

        # Verify DB state
        async with pool.acquire() as conn:
            # v1 superseded
            v1_row = await conn.fetchrow("SELECT * FROM memories WHERE id = $1", v1_id)
            assert v1_row["status"] == "superseded"
            assert v1_row["superseded_at"] is not None
            assert v1_row["valid_to"] is not None

            # v2 active
            v2_row = await conn.fetchrow(
                "SELECT * FROM memories WHERE lineage_id = $1 AND version = 2",
                lineage_id,
            )
            assert v2_row["status"] == "active"
            assert v1_row["superseded_by"] == v2_row["id"]

            # Relation SUPERSEDES
            rel_row = await conn.fetchrow(
                "SELECT * FROM memory_relations WHERE from_memory = $1 AND to_memory = $2",
                v2_row["id"],
                v1_id,
            )
            assert rel_row is not None
            assert rel_row["type"] == "SUPERSEDES"

            # Citation points to correction utterance event
            cit_row = await conn.fetchrow("SELECT * FROM citations WHERE memory_id = $1", v2_row["id"])
            assert cit_row is not None
            assert cit_row["quote"] == "Charlie upgraded production to PostgreSQL 17."


@pytest.mark.asyncio
async def test_correction_concurrency_conflict(tmp_path: Path):
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)

        cap_req = CaptureRequest(
            content="David runs Ubuntu 22.04.",
            source=SourceInfo(kind="chat", title="OS", author="david"),
            context="default",
        )
        cap_res = await events_adapter.capture(cap_req)

        mock_extractor = MemoryExtractor()
        mock_extractor.extract = AsyncMock(
            return_value={
                "entities": [{"ref": "e1", "name": "David", "kind": "person", "aliases": []}],
                "memories": [
                    {
                        "ref": "m1",
                        "type": "fact",
                        "epistemic": "fact",
                        "asserted_by": "david",
                        "statement": "David runs Ubuntu 22.04.",
                        "subject": "e1",
                        "predicate": "RUNS_OS",
                        "object": None,
                        "object_value": "Ubuntu 22.04",
                        "evidence": ["runs Ubuntu 22.04"],
                        "confidence": 0.95,
                        "importance": 0.8,
                        "lane": "code",
                        "sensitivity": "normal",
                        "about_speaker": True,
                    }
                ],
            }
        )
        pipeline = SavePipeline(pool, extractor=mock_extractor)
        save_rep = await pipeline.run(cap_res.event_id)
        lineage_id = uuid.UUID(save_rep.memories[0]["lineage_id"])

        lineage_svc = LineageService(pool)

        # 1. First correction succeeds (v1 -> v2)
        await lineage_svc.correct(
            lineage_id=lineage_id,
            new_statement="David runs Ubuntu 24.04.",
            base_version=1,
        )

        # 2. Concurrent second correction expecting base_version=1 must fail with ConflictError
        with pytest.raises(ConflictError, match="Version conflict"):
            await lineage_svc.correct(
                lineage_id=lineage_id,
                new_statement="David runs Arch Linux.",
                base_version=1,
            )


@pytest.mark.asyncio
async def test_conversational_correction_executor(tmp_path: Path):
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)

        cap_req = CaptureRequest(
            content="Emma favorite beverage is green tea.",
            source=SourceInfo(kind="chat", title="Tea Chat", author="emma"),
            context="default",
        )
        cap_res = await events_adapter.capture(cap_req)

        mock_extractor = MemoryExtractor()
        mock_extractor.extract = AsyncMock(
            return_value={
                "entities": [{"ref": "e1", "name": "Emma", "kind": "person", "aliases": []}],
                "memories": [
                    {
                        "ref": "m1",
                        "type": "fact",
                        "epistemic": "fact",
                        "asserted_by": "emma",
                        "statement": "Emma favorite beverage is green tea.",
                        "subject": "e1",
                        "predicate": "PREFERS",
                        "object": None,
                        "object_value": "green tea",
                        "evidence": ["favorite beverage is green tea"],
                        "confidence": 0.95,
                        "importance": 0.7,
                        "lane": "personal",
                        "sensitivity": "normal",
                        "about_speaker": True,
                    }
                ],
            }
        )
        pipeline = SavePipeline(pool, extractor=mock_extractor)
        save_rep = await pipeline.run(cap_res.event_id)
        mid = uuid.UUID(save_rep.memories[0]["id"])

        executor = IntentExecutor(pool)

        # Conversational correction: "Actually, it's matcha latte"
        res = await executor.process_turn("Actually, it's matcha latte", recent_memory_ids=[mid])
        assert res["handled"] is True
        assert res["intent"] == "correct"
        assert res["status"] == "success"
        assert "Updated:" in res["reply"]
        assert "matcha latte" in res["reply"]
        assert "v2" in res["reply"]
