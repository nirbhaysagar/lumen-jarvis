"""
Unit Tests for Context Compactor & Obsidian Vault Sync.
Verifies Markdown card export with [[Wikilinks]], episodic compaction watermarks,
and non-blocking prompt window preservation.
"""
import pytest
from pathlib import Path
import tempfile
from src.vault.vault_sync import ObsidianVaultSync
from src.compactor.compactor import ContextCompactor
from src.runtime.session import ConversationSession


def test_vault_export_fact_and_wikilinks():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = ObsidianVaultSync(vault_path=tmpdir)
        fact_data = {
            "id": "test_fact_uuid_123",
            "entity": "user_preference",
            "attribute": "tech_stack",
            "value": "I prefer TypeScript and PostgreSQL, and avoid MongoDB.",
            "valid_from": "2026-11-20T00:00:00Z",
            "valid_to": None,
        }
        file_path = vault.export_fact(fact_data)
        assert file_path.exists()
        content = file_path.read_text()
        assert "---" in content
        assert "id: test_fact_uuid_123" in content
        assert "[[TypeScript]]" in content
        assert "[[PostgreSQL]]" in content
        assert "[[MongoDB]]" in content
        assert "[[user_preference]]" in content


def test_vault_export_episode():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = ObsidianVaultSync(vault_path=tmpdir)
        file_path = vault.export_episode(
            session_id="conv_test_abc",
            summary="Agreed to build Lumen with True Concurrency.",
            turn_count=4,
            evacuated_turns=[
                {"role": "user", "content": "How do we handle concurrency?"},
                {"role": "assistant", "content": "We use a supervisor with an asyncio semaphore."},
            ],
        )
        assert file_path.exists()
        content = file_path.read_text()
        assert "session_id: conv_test_abc" in content
        assert "[[Lumen]]" in content
        assert "asyncio" in content


def test_compactor_watermark_evacuation():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = ObsidianVaultSync(vault_path=tmpdir)
        compactor = ContextCompactor(
            high_watermark_turns=4,
            low_watermark_turns=2,
            vault_sync=vault,
        )

        history = [
            {"role": "user", "content": "Turn 1: What is our DB?"},
            {"role": "assistant", "content": "Turn 2: PostgreSQL with pgvector."},
            {"role": "user", "content": "Turn 3: What is our queue?"},
            {"role": "assistant", "content": "Turn 4: Redis."},
        ]

        assert compactor.should_compact(history) is True

        compacted, summary = compactor.compact("session_test_1", history)
        assert len(compacted) == 3  # 1 summary anchor + 2 recent turns
        assert compacted[0]["role"] == "system"
        assert "[PRIOR CONTEXT COMPACTED:" in compacted[0]["content"]
        assert compacted[1]["content"] == "Turn 3: What is our queue?"
        assert compacted[2]["content"] == "Turn 4: Redis."

        # Verify episode note was created on disk
        stats = vault.get_vault_stats()
        assert stats["episodes"] == 1


@pytest.mark.asyncio
async def test_session_compact_command():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = ObsidianVaultSync(vault_path=tmpdir)
        compactor = ContextCompactor(vault_sync=vault)
        session = ConversationSession(vault_sync=vault, compactor=compactor)

        # Seed history
        session.history = [
            {"role": "user", "content": "Hey Jarvis"},
            {"role": "assistant", "content": "Good day, Sir."},
            {"role": "user", "content": "What is our architecture?"},
            {"role": "assistant", "content": "Hexagonal ports and adapters."},
        ]

        tokens = []
        async for token in session.process_turn("/compact"):
            tokens.append(token)

        reply = "".join(tokens)
        assert "🧹 Context Compaction Complete" in reply
        assert len(session.history) <= 3
