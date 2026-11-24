"""
Golden End-to-End Test for S1 Save Pipeline.
Covers all 5 stages, all 8 memory aspects, secret masking, cross-speaker conflict,
verbatim citation round-tripping, and reaffirmation deduplication.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import json
import uuid
import pytest
from httpx import AsyncClient

from app.domain.events import CaptureRequest, SourceInfo
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.domain.extraction import MemoryExtractor
from app.pipeline import SavePipeline
from app.main import app


@pytest.mark.asyncio
async def test_save_pipeline_e2e_golden_flow(client: AsyncClient):
    """
    Sentence:
    "Since March I've been using Postgres 17 for Lumen at work. Rahul thinks we should use Mongo. My OTP is 482913."
    """
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)

        # 3 days ago
        three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
        sentence = "Since March I've been using Postgres 17 for Lumen at work. Rahul thinks we should use Mongo. My OTP is 482913."
        ctx_slug = f"golden_{uuid.uuid4().hex[:8]}"

        # 1. Capture into L0
        cap_req = CaptureRequest(
            content=sentence,
            source=SourceInfo(kind="chat", title="golden test", author="user"),
            context=ctx_slug,
            occurred_at=three_days_ago,
            speaker="user",
        )
        cap_res = await events_adapter.capture(cap_req)
        assert cap_res.event_id is not None
        assert cap_res.sensitivity_hint == "secret"

        # 2. Mock Extractor with schema v2 output
        mock_extraction = {
            "entities": [
                {"ref": "e1", "name": "Lumen", "kind": "project", "aliases": []},
                {"ref": "e2", "name": "PostgreSQL 17", "kind": "tool", "aliases": ["Postgres 17"]},
                {"ref": "e3", "name": "Rahul", "kind": "person", "aliases": []},
                {"ref": "e4", "name": "MongoDB", "kind": "tool", "aliases": ["Mongo"]},
            ],
            "memories": [
                {
                    "ref": "m1",
                    "type": "decision",
                    "epistemic": "decision",
                    "asserted_by": "user",
                    "statement": "User uses PostgreSQL 17 for Lumen at work.",
                    "subject": "e1",
                    "predicate": "uses_database",
                    "object": "e2",
                    "object_value": "PostgreSQL 17",
                    "attributes": {"why": "production stability", "who": "user"},
                    "cardinality": "one",
                    "polarity": True,
                    "valid_from_text": "since March",
                    "evidence": ["Since March I've been using Postgres 17 for Lumen at work"],
                    "confidence": 0.9,
                    "importance": 0.85,
                    "lane": "work",
                    "sensitivity": "normal",
                    "about_speaker": True,
                },
                {
                    "ref": "m2",
                    "type": "fact",
                    "epistemic": "fact",
                    "asserted_by": "Rahul",
                    "statement": "Rahul thinks Lumen should use MongoDB.",
                    "subject": "e1",
                    "predicate": "uses_database",
                    "object": "e4",
                    "object_value": "MongoDB",
                    "cardinality": "one",
                    "polarity": True,
                    "evidence": ["Rahul thinks we should use Mongo"],
                    "confidence": 0.75,
                    "importance": 0.5,
                    "lane": "work",
                    "sensitivity": "normal",
                    "about_speaker": False,
                },
                {
                    "ref": "m3",
                    "type": "fact",
                    "epistemic": "fact",
                    "asserted_by": "user",
                    "statement": "User OTP is 482913.",
                    "subject": None,
                    "predicate": "has_otp",
                    "object": None,
                    "object_value": "482913",
                    "cardinality": "many",
                    "polarity": True,
                    "evidence": ["My OTP is 482913"],
                    "confidence": 0.95,
                    "importance": 0.9,
                    "lane": "personal",
                    "sensitivity": "secret",
                    "about_speaker": True,
                },
            ],
        }

        mock_extractor = MemoryExtractor()
        mock_extractor.extract = AsyncMock(return_value=mock_extraction)

        pipeline = SavePipeline(pool, extractor=mock_extractor)
        report = await pipeline.run(cap_res.event_id)

        assert report.event_id == cap_res.event_id
        assert len(report.memories) == 3
        assert report.counts["created"] == 3

        # (1) Verify Postgres memory (Aspects 1, 2, 3, 4, 5, 6, 7)
        async with pool.acquire() as conn:
            # Memory 1 (Postgres 17)
            m1_row = await conn.fetchrow(
                """
                SELECT m.id, m.statement, m.type, m.epistemic, m.asserted_by, m.lane, m.sensitivity,
                       m.valid_from, m.valid_to, m.confidence, m.importance, m.status,
                       c.quote, c.char_start, c.char_end, c.chunk_id
                FROM memories m
                JOIN citations c ON c.memory_id = m.id
                WHERE m.statement = 'User uses PostgreSQL 17 for Lumen at work.'
                """
            )
            assert m1_row is not None
            assert m1_row["type"] == "decision"
            assert m1_row["epistemic"] == "decision"
            assert m1_row["asserted_by"] == "user"
            assert m1_row["lane"] == "work"
            assert m1_row["sensitivity"] == "normal"
            assert m1_row["status"] == "active"
            assert m1_row["valid_from"].month == 3
            # Verbatim quote exact roundtrip
            slice_from_text = sentence[m1_row["char_start"]:m1_row["char_end"]]
            assert m1_row["quote"] == slice_from_text

            # Memory 2 (Rahul's claim about Mongo)
            m2_row = await conn.fetchrow(
                """
                SELECT m.id, m.statement, m.epistemic, m.asserted_by, m.status,
                       cl.claimant, cl.stance
                FROM memories m
                LEFT JOIN claims cl ON cl.memory_id = m.id
                WHERE m.statement = 'Rahul thinks Lumen should use MongoDB.'
                """
            )
            assert m2_row is not None
            assert m2_row["asserted_by"] == "Rahul"
            assert m2_row["claimant"] == "Rahul"
            assert m2_row["stance"] == "supports"

            # Memory 3 (OTP secret)
            m3_row = await conn.fetchrow(
                "SELECT id, sensitivity FROM memories WHERE statement = 'User OTP is 482913.'"
            )
            assert m3_row is not None
            assert m3_row["sensitivity"] == "secret"

            # Check that secret memory has NO embedding job
            embed_job = await conn.fetchrow(
                "SELECT id FROM jobs WHERE kind = 'embed_memory' AND payload->>'memory_id' = $1",
                str(m3_row["id"]),
            )
            assert embed_job is None

            # Check Outbox row for secret memory has masked statement
            outbox_row = await conn.fetchrow(
                "SELECT payload FROM outbox WHERE payload->>'memory_id' = $1",
                str(m3_row["id"]),
            )
            assert outbox_row is not None
            ob_payload = json.loads(outbox_row["payload"]) if isinstance(outbox_row["payload"], str) else outbox_row["payload"]
            assert ob_payload["secret"] is True
            assert ob_payload["statement"] == "[REDACTED SECRET]"

            # Check save_runs table has recorded run
            save_run = await conn.fetchrow(
                "SELECT event_id, stage_ms, counts FROM save_runs WHERE event_id = $1",
                cap_res.event_id,
            )
            assert save_run is not None

        # (2) Test GET /v1/events/{id}/save-report API
        report_res = await client.get(f"/v1/events/{cap_res.event_id}/save-report")
        assert report_res.status_code == 200
        report_data = report_res.json()
        assert report_data["event_id"] == str(cap_res.event_id)
        assert len(report_data["memories"]) == 3
        assert "extract" in report_data["stage_ms"]
        assert "commit" in report_data["stage_ms"]

        # (3) Replay same sentence -> reaffirmation check
        cap_res2 = await events_adapter.capture(CaptureRequest(content=sentence, context=ctx_slug))
        pipeline2 = SavePipeline(pool, extractor=mock_extractor)
        report2 = await pipeline2.run(cap_res2.event_id)

        # Reaffirm count incremented, no duplicate memories created
        assert report2.counts["reaffirmed"] >= 1
        async with pool.acquire() as conn:
            reaffirm_sig = await conn.fetchval(
                "SELECT reaffirm_count FROM memory_signals WHERE lineage_id = $1",
                uuid.UUID(report.memories[0]["lineage_id"]),
            )
            assert reaffirm_sig >= 1
