"""
Integration Tests for Structured Extraction & Memory Application.
Verifies Milestone M3 & M4 requirements:
1. Verbatim evidence verification (exact character spans).
2. Entity resolution and canonical alias matching.
3. Memory insertion across canonical types and epistemic modes.
4. Non-destructive versioning (SUPERSEDES relation, lineage_id stability).
"""
import uuid
import pytest
from app.domain.events import CaptureRequest
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.domain.apply import MemoryApplier
from app.main import app


@pytest.mark.asyncio
async def test_extraction_and_application():
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)

        raw_text = "We decided to move Lumen to Postgres 17 because SQLite locked under concurrent writes."
        cap_res = await events_adapter.capture(
            CaptureRequest(
                content=raw_text,
                source={"kind": "chat", "title": "db architecture", "author": "user"},
                idempotency_key=f"test_extr_{uuid.uuid4()}",
            )
        )
        assert cap_res.idempotent_hit is False
        event_id = cap_res.event_id
        context_id = cap_res.context_id

        extraction_data = {
            "entities": [
                {"ref": "e1", "name": "Lumen", "kind": "project"},
                {"ref": "e2", "name": "Postgres 17", "kind": "tool", "aliases": ["Postgres", "PG17"]},
                {"ref": "e3", "name": "SQLite", "kind": "tool"}
            ],
            "memories": [
                {
                    "ref": "m1",
                    "type": "decision",
                    "epistemic": "decision",
                    "asserted_by": "user",
                    "statement": "Nirbhay decided Lumen will use PostgreSQL 17 instead of SQLite.",
                    "subject": "e1",
                    "predicate": "primary_database",
                    "object": "e2",
                    "cardinality": "one",
                    "polarity": True,
                    "change_cue": True,
                    "evidence": ["we decided to move Lumen to Postgres 17"],
                    "confidence": 0.95,
                    "importance": 0.8,
                    "relations": [{"type": "CAUSED_BY", "to": "m2"}]
                },
                {
                    "ref": "m2",
                    "type": "fact",
                    "epistemic": "belief",
                    "asserted_by": "user",
                    "statement": "SQLite locked under concurrent writes.",
                    "subject": "e3",
                    "predicate": "limitation",
                    "object_value": "concurrent writes",
                    "cardinality": "many",
                    "polarity": True,
                    "evidence": ["SQLite locked under concurrent writes"],
                    "confidence": 0.8,
                    "importance": 0.6
                }
            ]
        }

        # Apply extraction
        res = await applier.apply_extraction(event_id, context_id, raw_text, extraction_data)
        assert res["memories_created"] == 2

        # Verify memories in DB
        async with pool.acquire() as conn:
            mems = await conn.fetch(
                "SELECT id, lineage_id, version, type, epistemic, statement, status FROM memories WHERE created_event = $1",
                event_id,
            )
            assert len(mems) == 2
            mem_dict = {m["type"]: m for m in mems}
            assert "decision" in mem_dict
            assert "fact" in mem_dict
            assert mem_dict["decision"]["status"] == "active"

            # Verify citations in DB
            cits = await conn.fetch(
                "SELECT id, quote, verified, char_start, char_end FROM citations WHERE event_id = $1",
                event_id,
            )
            assert len(cits) == 2
            for c in cits:
                assert c["verified"] is True
                assert c["char_end"] > c["char_start"]
                # Verify exact character slice from raw_text
                assert raw_text[c["char_start"]:c["char_end"]].lower() == c["quote"].lower()

            # Verify relations in DB (CAUSED_BY + MENTIONS)
            rels = await conn.fetch(
                "SELECT type FROM memory_relations WHERE created_event = $1",
                event_id,
            )
            rel_types = {r["type"] for r in rels}
            assert "CAUSED_BY" in rel_types
            assert "MENTIONS" in rel_types


@pytest.mark.asyncio
async def test_non_destructive_supersede_versioning():
    """Verify that an UPDATE creates v2 in the same lineage and adds SUPERSEDES relation."""
    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)
        applier = MemoryApplier(pool)

        # Event 1: Initial decision
        text1 = "Lumen's database is SQLite."
        cap1 = await events_adapter.capture(
            CaptureRequest(content=text1, source={"kind": "chat"}, idempotency_key=f"t1_{uuid.uuid4()}")
        )
        data1 = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m1",
                "type": "decision",
                "epistemic": "decision",
                "asserted_by": "user",
                "statement": "Lumen database is SQLite.",
                "subject": "e1",
                "predicate": "primary_database",
                "cardinality": "one",
                "evidence": ["Lumen's database is SQLite."],
                "confidence": 0.9,
                "importance": 0.8,
            }]
        }
        await applier.apply_extraction(cap1.event_id, cap1.context_id, text1, data1)

        # Event 2: Superseding decision
        text2 = "We moved Lumen to Postgres 17 because SQLite locked."
        cap2 = await events_adapter.capture(
            CaptureRequest(content=text2, source={"kind": "chat"}, idempotency_key=f"t2_{uuid.uuid4()}")
        )
        data2 = {
            "entities": [{"ref": "e1", "name": "Lumen", "kind": "project"}],
            "memories": [{
                "ref": "m2",
                "type": "decision",
                "epistemic": "decision",
                "asserted_by": "user",
                "statement": "Lumen database is Postgres 17.",
                "subject": "e1",
                "predicate": "primary_database",
                "cardinality": "one",
                "change_cue": True,
                "evidence": ["We moved Lumen to Postgres 17"],
                "confidence": 0.95,
                "importance": 0.8,
            }]
        }
        await applier.apply_extraction(cap2.event_id, cap2.context_id, text2, data2)

        # Verify lineage: 2 rows with same lineage_id, versions 1 and 2
        async with pool.acquire() as conn:
            versions = await conn.fetch(
                """
                SELECT id, lineage_id, version, statement, status, superseded_at, superseded_by
                FROM memories
                WHERE created_event IN ($1, $2)
                ORDER BY version ASC
                """,
                cap1.event_id,
                cap2.event_id,
            )
            assert len(versions) == 2
            v1, v2 = versions[0], versions[1]
            assert v1["lineage_id"] == v2["lineage_id"]
            assert v1["version"] == 1
            assert v2["version"] == 2
            # v1 superseded
            assert v1["superseded_at"] is not None
            assert v1["superseded_by"] == v2["id"]
            # v2 active
            assert v2["superseded_at"] is None
            assert v2["status"] == "active"

            # Check SUPERSEDES relation exists
            sup_rel = await conn.fetchrow(
                "SELECT type, from_memory, to_memory FROM memory_relations WHERE type = 'SUPERSEDES'"
            )
            assert sup_rel is not None
            assert sup_rel["from_memory"] == v2["id"]
            assert sup_rel["to_memory"] == v1["id"]
