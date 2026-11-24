"""
Unit & Integration Tests for In-Built Obsidian Knowledge Vault Projector (Milestone O1).
Verifies:
1. Deterministic Vault Rebuild: generates memories, entities, and daily notes from PostgreSQL.
2. Memory note format: YAML frontmatter, editable block, citations, and version history.
3. Entity note format: YAML frontmatter aliases, active facts, Dataview-compatible relations, backlinks.
4. Dynamic Wikilinking: automatically links entity mentions without hard-coded lists.
5. Bit-identical idempotence: running rebuild twice produces identical files.
6. Path traversal security: safe_vault_target rejects paths outside the vault root.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
import pytest
from unittest.mock import AsyncMock

from app.domain.events import CaptureRequest, SourceInfo
from app.adapters.storage.pg_events import PostgresEventsAdapter
from app.domain.extraction import MemoryExtractor
from app.pipeline import SavePipeline
from app.vault.projector import (
    VaultProjector,
    wikilinkify,
    safe_vault_target,
    compute_content_hash,
)
from app.main import app


def test_safe_vault_target(tmp_path: Path):
    vault_dir = tmp_path / "safe_test_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    # Valid subpath
    valid = vault_dir / "memories" / "note.md"
    assert safe_vault_target(vault_dir, valid) == valid.resolve()

    # Path traversal attack
    attack = vault_dir / ".." / "escaped.md"
    with pytest.raises(ValueError, match="Path traversal detected"):
        safe_vault_target(vault_dir, attack)


def test_dynamic_wikilinkify():
    entity_map = [
        ("postgresql 17", "PostgreSQL 17"),
        ("postgresql", "PostgreSQL"),
        ("postgres", "PostgreSQL"),
        ("alice", "Alice"),
    ]
    # Longest match first: "PostgreSQL 17" shouldn't become "[[PostgreSQL]] 17"
    text1 = "Alice said Lumen uses PostgreSQL 17 for storage."
    linked1 = wikilinkify(text1, entity_map)
    assert linked1 == "[[Alice]] said Lumen uses [[PostgreSQL 17]] for storage."

    # Avoid double-linking already linked text
    text2 = "[[Alice]] prefers [[PostgreSQL]] over other databases."
    linked2 = wikilinkify(text2, entity_map)
    assert linked2 == "[[Alice]] prefers [[PostgreSQL]] over other databases."


@pytest.mark.asyncio
async def test_vault_projector_deterministic_rebuild(tmp_path: Path):
    vault_dir = tmp_path / "test_vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    async with app.router.lifespan_context(app):
        pool = app.state.storage.pool
        events_adapter = PostgresEventsAdapter(pool)

        # 1. Ingest test memories via SavePipeline
        cap_req = CaptureRequest(
            content="Alice founded Cerebro Dynamics and prefers PostgreSQL for databases.",
            source=SourceInfo(kind="chat", title="Founding Chat", author="alice"),
            context="ctx_vault_test",
        )
        cap_res = await events_adapter.capture(cap_req)

        mock_extractor = MemoryExtractor()
        mock_extractor.extract = AsyncMock(
            return_value={
                "entities": [
                    {"ref": "e1", "name": "Alice", "kind": "person", "aliases": ["Ali"]},
                    {"ref": "e2", "name": "Cerebro Dynamics", "kind": "org", "aliases": ["Cerebro"]},
                    {"ref": "e3", "name": "PostgreSQL", "kind": "tool", "aliases": ["Postgres", "psql"]},
                ],
                "memories": [
                    {
                        "ref": "m1",
                        "type": "fact",
                        "epistemic": "fact",
                        "asserted_by": "alice",
                        "statement": "Alice founded Cerebro Dynamics.",
                        "subject": "e1",
                        "predicate": "FOUNDED",
                        "object": "e2",
                        "object_value": "Cerebro Dynamics",
                        "evidence": ["Alice founded Cerebro Dynamics"],
                        "confidence": 0.98,
                        "importance": 0.9,
                        "lane": "work",
                        "sensitivity": "normal",
                        "about_speaker": True,
                    },
                    {
                        "ref": "m2",
                        "type": "fact",
                        "epistemic": "belief",
                        "asserted_by": "alice",
                        "statement": "Alice prefers PostgreSQL for databases.",
                        "subject": "e1",
                        "predicate": "PREFERS",
                        "object": "e3",
                        "object_value": "PostgreSQL",
                        "evidence": ["prefers PostgreSQL for databases"],
                        "confidence": 0.95,
                        "importance": 0.7,
                        "lane": "code",
                        "sensitivity": "normal",
                        "about_speaker": True,
                    },
                ],
            }
        )

        pipeline = SavePipeline(pool, extractor=mock_extractor)
        save_report = await pipeline.run(cap_res.event_id)
        assert len(save_report.memories) == 2

        # 2. Run Vault Projector Rebuild
        projector = VaultProjector(pool, vault_dir=vault_dir)
        stats = await projector.rebuild()
        assert stats["memories"] == 2
        assert stats["entities"] >= 3
        assert stats["daily"] >= 1

        # 3. Verify Memory Notes
        memory_files = list((vault_dir / "memories").glob("**/*.md"))
        assert len(memory_files) == 2
        m_sample = memory_files[0].read_text(encoding="utf-8")
        assert "lumen_id:" in m_sample
        assert "lineage_id:" in m_sample
        assert "<!-- lumen:editable:start -->" in m_sample
        assert "<!-- lumen:editable:end -->" in m_sample
        assert "## Version History" in m_sample

        # 4. Verify Entity Notes & Dataview Relations
        alice_note = vault_dir / "entities" / "Alice.md"
        assert alice_note.exists()
        alice_text = alice_note.read_text(encoding="utf-8")
        assert "aliases:" in alice_text
        assert "Ali" in alice_text
        assert "## Current Facts" in alice_text
        assert "## Relations" in alice_text
        assert "## Mentioned In" in alice_text
        # Dataview typed relations format check
        assert "FOUNDED:: [[Cerebro Dynamics]]" in alice_text or "PREFERS:: [[PostgreSQL]]" in alice_text

        # 5. Verify Daily Note
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily_note = vault_dir / "daily" / f"{today_str}.md"
        assert daily_note.exists()
        assert "Daily Memory Log:" in daily_note.read_text(encoding="utf-8")

        # 6. Verify Bit-Identical Idempotence
        snapshot_1 = {p: p.read_bytes() for p in vault_dir.glob("**/*.md")}
        stats_2 = await projector.rebuild()
        assert stats_2 == stats
        snapshot_2 = {p: p.read_bytes() for p in vault_dir.glob("**/*.md")}
        assert snapshot_1 == snapshot_2
