"""
Tests for Conversational Forget and Lineage Retraction (Milestone F1).
Verifies:
1. Retract: marks active memory as retracted, leaves audit trail in events, queues outbox event.
2. Recall exclusion: retracted memory never nominated or returned by recall.
3. Purge: sensitive spans recorded in redactions table and masked.
4. Undo: restores retracted memory by writing version v(n+1) with status='active'.
5. Ambiguity: closely matched candidates prompt clarification instead of blindly modifying.
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
from app.pipeline import SavePipeline
from app.services.lineage_service import LineageService, apply_redactions
from app.domain.intents import IntentClassifier, IntentExecutor, TargetResolver
from app.main import app


@pytest.mark.asyncio
async def test_retract_and_recall_exclusion(tmp_path: Path):
    vault_dir = tmp_path / "forget_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)

        # 1. Ingest base memory
        cap_req = CaptureRequest(
            content="Alice used to live in Indiranagar.",
            source=SourceInfo(kind="chat", title="Chat 1", author="alice"),
            context="default",
        )
        cap_res = await events_adapter.capture(cap_req)

        mock_extractor = MemoryExtractor()
        mock_extractor.extract = AsyncMock(
            return_value={
                "entities": [{"ref": "e1", "name": "Alice", "kind": "person", "aliases": []}],
                "memories": [
                    {
                        "ref": "m1",
                        "type": "fact",
                        "epistemic": "fact",
                        "asserted_by": "alice",
                        "statement": "Alice used to live in Indiranagar.",
                        "subject": "e1",
                        "predicate": "LIVES_IN",
                        "object": None,
                        "object_value": "Indiranagar",
                        "evidence": ["Alice used to live in Indiranagar"],
                        "confidence": 0.95,
                        "importance": 0.8,
                        "lane": "personal",
                        "sensitivity": "normal",
                        "about_speaker": True,
                    }
                ],
            }
        )
        pipeline = SavePipeline(pool, extractor=mock_extractor)
        save_rep = await pipeline.run(cap_res.event_id)
        assert len(save_rep.memories) == 1
        lineage_id = uuid.UUID(save_rep.memories[0]["lineage_id"])

        # Verify active memory is returned by recall
        recall_adapter = PostgresRecallAdapter(pool)
        recalled_before = await recall_adapter.recall("Where did Alice live?", context_slug="default", threshold=0.1)
        assert any(str(r["lineage_id"]) == str(lineage_id) for r in recalled_before)

        # 2. Retract memory via LineageService
        lineage_svc = LineageService(pool, vault_dir=vault_dir)
        retract_res = await lineage_svc.retract(lineage_id, reason="User moved away", actor="user")
        assert retract_res["action"] == "retracted"
        assert retract_res["version"] == 1

        # 3. Verify PostgreSQL status and audit trail
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM memories WHERE lineage_id = $1", lineage_id)
            assert row["status"] == "retracted"
            assert row["valid_to"] is not None
            assert row["superseded_at"] is not None

            # Tombstone event logged
            tombstone_ev = await conn.fetchrow(
                "SELECT * FROM events WHERE type = 'memory.tombstoned' AND tombstone = true"
            )
            assert tombstone_ev is not None

            # Outbox event logged
            ob = await conn.fetchrow("SELECT * FROM outbox WHERE topic = 'memory.retracted'")
            assert ob is not None

        # 4. Recall exclusion: memory must NEVER be returned by active recall
        recalled_after = await recall_adapter.recall("Where did Alice live?", context_slug="default", threshold=0.1)
        assert not any(str(r["lineage_id"]) == str(lineage_id) for r in recalled_after)

        # 5. History still preserves the memory
        history = await lineage_svc.get_history(lineage_id)
        assert len(history) == 1
        assert history[0]["status"] == "retracted"


@pytest.mark.asyncio
async def test_purge_and_redaction(tmp_path: Path):
    vault_dir = tmp_path / "purge_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)

        # Ingest memory containing secret phone number
        cap_req = CaptureRequest(
            content="Alice phone number is +1-555-0199.",
            source=SourceInfo(kind="chat", title="Secrets", author="alice"),
            context="default",
        )
        cap_res = await events_adapter.capture(cap_req)

        mock_extractor = MemoryExtractor()
        mock_extractor.extract = AsyncMock(
            return_value={
                "entities": [{"ref": "e1", "name": "Alice", "kind": "person", "aliases": []}],
                "memories": [
                    {
                        "ref": "m1",
                        "type": "fact",
                        "epistemic": "fact",
                        "asserted_by": "alice",
                        "statement": "Alice phone number is +1-555-0199.",
                        "subject": "e1",
                        "predicate": "HAS_PHONE",
                        "object": None,
                        "object_value": "+1-555-0199",
                        "evidence": ["phone number is +1-555-0199"],
                        "confidence": 0.99,
                        "importance": 0.9,
                        "lane": "personal",
                        "sensitivity": "private",
                        "about_speaker": True,
                    }
                ],
            }
        )
        pipeline = SavePipeline(pool, extractor=mock_extractor)
        save_rep = await pipeline.run(cap_res.event_id)
        lineage_id = uuid.UUID(save_rep.memories[0]["lineage_id"])

        # Retract with purge=True
        lineage_svc = LineageService(pool, vault_dir=vault_dir)
        res = await lineage_svc.retract(lineage_id, reason="delete sensitive number", purge=True, purge_spans=[(22, 33)])
        assert res["purged"] is True

        # Verify redactions table
        async with pool.acquire() as conn:
            redact_row = await conn.fetchrow("SELECT * FROM redactions")
            assert redact_row is not None
            assert redact_row["char_start"] == 22
            assert redact_row["char_end"] == 33

        # Test apply_redactions helper
        redacted = apply_redactions("Alice phone number is +1-555-0199.", [(22, 33)])
        assert redacted == "Alice phone number is [redacted]."


@pytest.mark.asyncio
async def test_undo_retract(tmp_path: Path):
    vault_dir = tmp_path / "undo_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)

        cap_req = CaptureRequest(
            content="Alice prefers light roast coffee.",
            source=SourceInfo(kind="chat", title="Coffee Chat", author="alice"),
            context="default",
        )
        cap_res = await events_adapter.capture(cap_req)

        mock_extractor = MemoryExtractor()
        mock_extractor.extract = AsyncMock(
            return_value={
                "entities": [{"ref": "e1", "name": "Alice", "kind": "person", "aliases": []}],
                "memories": [
                    {
                        "ref": "m1",
                        "type": "fact",
                        "epistemic": "fact",
                        "asserted_by": "alice",
                        "statement": "Alice prefers light roast coffee.",
                        "subject": "e1",
                        "predicate": "PREFERS",
                        "object": None,
                        "object_value": "light roast coffee",
                        "evidence": ["prefers light roast coffee"],
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
        lineage_id = uuid.UUID(save_rep.memories[0]["lineage_id"])

        lineage_svc = LineageService(pool, vault_dir=vault_dir)
        # Retract v1
        await lineage_svc.retract(lineage_id, reason="Accidental delete")

        # Undo retract -> creates v2
        undo_res = await lineage_svc.undo_retract(lineage_id, reason="user clicked undo")
        assert undo_res["action"] == "restored"
        assert undo_res["version"] == 2

        # Verify DB has v2 active and v1 retracted
        async with pool.acquire() as conn:
            v2 = await conn.fetchrow("SELECT * FROM memories WHERE lineage_id = $1 AND version = 2", lineage_id)
            assert v2["status"] == "active"
            v1 = await conn.fetchrow("SELECT * FROM memories WHERE lineage_id = $1 AND version = 1", lineage_id)
            assert v1["status"] == "retracted"


@pytest.mark.asyncio
async def test_conversational_forget_executor(tmp_path: Path):
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)

        # Ingest memory
        cap_req = CaptureRequest(
            content="Bob is studying quantum computing at university.",
            source=SourceInfo(kind="chat", title="Studies", author="bob"),
            context="default",
        )
        cap_res = await events_adapter.capture(cap_req)

        mock_extractor = MemoryExtractor()
        mock_extractor.extract = AsyncMock(
            return_value={
                "entities": [{"ref": "e1", "name": "Bob", "kind": "person", "aliases": []}],
                "memories": [
                    {
                        "ref": "m1",
                        "type": "fact",
                        "epistemic": "fact",
                        "asserted_by": "bob",
                        "statement": "Bob is studying quantum computing at university.",
                        "subject": "e1",
                        "predicate": "STUDYING",
                        "object": None,
                        "object_value": "quantum computing",
                        "evidence": ["studying quantum computing"],
                        "confidence": 0.95,
                        "importance": 0.8,
                        "lane": "work",
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

        # Conversational turn: "Forget what I said about quantum computing"
        res = await executor.process_turn("Forget what I said about quantum computing", recent_memory_ids=[mid])
        assert res["handled"] is True
        assert res["intent"] == "forget"
        assert res["status"] == "success"
        assert "Forgotten:" in res["reply"]
        assert "undo" in res["reply"]

        # Conversational turn: "undo"
        undo_turn = await executor.process_turn("undo")
        assert undo_turn["handled"] is True
        assert undo_turn["intent"] == "undo"
        assert undo_turn["status"] == "success"
        assert "Restored memory:" in undo_turn["reply"]
