"""
Tests for Overnight Consolidation Daemon and Morning Intelligence Digest Generator.
"""
import pytest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.vault.vault_sync import ObsidianVaultSync
from src.consolidation.daemon import ConsolidationDaemon
from src.memory_client.client import MemoryServiceClient


@pytest.fixture
def temp_vault(tmp_path: Path):
    vault_dir = tmp_path / "test_vault"
    vault_dir.mkdir()
    return ObsidianVaultSync(vault_path=vault_dir)


def test_vault_export_digest(temp_vault: ObsidianVaultSync):
    """Verify Morning Intelligence Digest generation with Markdown frontmatter and [[Wikilinks]]."""
    report = {
        "conflicts_detected": 2,
        "alias_candidates_found": 1,
        "orphans_detected": 4,
        "proposals_staged": 3,
    }
    pending_proposals = [
        {
            "id": str(uuid.uuid4()),
            "proposal_type": "CONTRADICTION_UPDATE",
            "subject_raw": "User",
            "predicate_raw": "PREFERS vs DISLIKES",
            "object_raw": "VS Code",
            "conflict_reason": "Opposing predicate collision detected between PREFERS and DISLIKES",
        }
    ]
    active_tasks = [
        {
            "task_id": "task_123",
            "name": "Codebase Scanner",
            "state": "RUNNING",
            "fractional_progress": 0.6,
        }
    ]

    digest_path = temp_vault.export_digest(
        date_str="2026-11-20",
        report=report,
        pending_proposals=pending_proposals,
        active_tasks=active_tasks,
    )

    assert digest_path.exists()
    content = digest_path.read_text(encoding="utf-8")

    assert "type: morning_digest" in content
    assert "conflicts_detected: 2" in content
    assert "🌅 Lumen Morning Intelligence Digest — `2026-11-20`" in content
    assert "[[User]]" in content
    assert "[[VS Code]]" in content
    assert "Codebase Scanner" in content
    assert "/approve-proposal" in content

    # Stats check
    stats = temp_vault.get_vault_stats()
    assert stats["digests"] == 1


@pytest.mark.asyncio
async def test_consolidation_daemon_job(temp_vault: ObsidianVaultSync):
    """Verify ConsolidationDaemon coordinates MemoryClient and VaultSync to produce a digest."""
    mock_client = MagicMock()
    mock_client.run_consolidation = AsyncMock(
        return_value={
            "conflicts_detected": 1,
            "alias_candidates_found": 2,
            "orphans_detected": 5,
            "proposals_staged": 3,
        }
    )
    mock_client.list_review_queue = AsyncMock(
        return_value=[
            {
                "id": str(uuid.uuid4()),
                "proposal_type": "ALIAS_MERGE",
                "subject_raw": "psql",
                "predicate_raw": "EQUIVALENT_TO",
                "object_raw": "PostgreSQL",
                "conflict_reason": "High embedding similarity (0.95)",
            }
        ]
    )

    daemon = ConsolidationDaemon(
        memory_client=mock_client,
        vault_sync=temp_vault,
    )

    result = await daemon.run_consolidation_job()

    assert result["status"] == "SUCCESS"
    assert result["pending_proposals_count"] == 1
    assert daemon.last_digest_path is not None
    assert daemon.last_digest_path.exists()

    content = daemon.last_digest_path.read_text(encoding="utf-8")
    assert "High embedding similarity (0.95)" in content
    assert "[[psql]]" in content


@pytest.mark.asyncio
async def test_live_consolidation_daemon_flow(temp_vault: ObsidianVaultSync):
    """End-to-end integration test against live Memory Engine daemon on port 8000."""
    client = MemoryServiceClient(base_url="http://localhost:8000")
    daemon = ConsolidationDaemon(
        memory_client=client,
        vault_sync=temp_vault,
    )

    result = await daemon.run_consolidation_job()

    assert result["status"] == "SUCCESS"
    assert daemon.last_digest_path is not None
    assert daemon.last_digest_path.exists()

    await client.close()
