"""
Tests for Transactional Outbox Worker and Obsidian Vault Synchronization.
Verifies asynchronous, fault-tolerant replication from PostgreSQL to Obsidian.
"""
import pytest
import shutil
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.vault.vault_sync import ObsidianVaultSync
from src.vault.outbox_worker import OutboxWorker
from src.memory_client.client import MemoryServiceClient


@pytest.fixture
def temp_vault(tmp_path: Path):
    vault_dir = tmp_path / "test_vault"
    vault_dir.mkdir()
    return ObsidianVaultSync(vault_path=vault_dir)


@pytest.mark.asyncio
async def test_vault_sync_export_entity_and_triple(temp_vault: ObsidianVaultSync):
    """Verify Markdown note generation for entities and bidirectional wikilink triples."""
    # 1. Export Entity
    entity_data = {
        "id": str(uuid.uuid4()),
        "canonical_name": "PostgreSQL",
        "category": "database",
        "aliases": ["psql", "Postgres"],
        "description": "PostgreSQL is an open-source relational database management system.",
    }
    entity_path = temp_vault.export_entity(entity_data)
    assert entity_path.exists()
    content = entity_path.read_text(encoding="utf-8")
    assert "# [[PostgreSQL]]" in content
    assert "category/database" in content
    assert "[[psql]]" in content
    assert "[[Postgres]]" in content

    # 2. Export Triple
    triple_data = {
        "id": str(uuid.uuid4()),
        "subject_name": "Lumen",
        "predicate": "USES",
        "object_name": "PostgreSQL",
        "confidence": 0.98,
        "valid_to": None,
    }
    sub_path, obj_path = temp_vault.export_triple(triple_data)
    assert sub_path.exists()
    assert obj_path.exists()

    sub_content = sub_path.read_text(encoding="utf-8")
    obj_content = obj_path.read_text(encoding="utf-8")

    # Subject should have outbound link
    assert "Outbound (→)" in sub_content
    assert "[[USES]]" in sub_content
    assert "[[PostgreSQL]]" in sub_content

    # Object should have inbound link
    assert "Inbound (←)" in obj_content
    assert "[[USES]]" in obj_content
    assert "[[Lumen]]" in obj_content


@pytest.mark.asyncio
async def test_outbox_worker_drains_pending_items(temp_vault: ObsidianVaultSync):
    """Verify OutboxWorker drains pending items and acknowledges them via MemoryClient."""
    mock_client = MagicMock()
    fact_id = str(uuid.uuid4())
    entity_id = str(uuid.uuid4())
    triple_id = str(uuid.uuid4())

    pending_items = [
        {
            "id": str(uuid.uuid4()),
            "aggregate_type": "FACT",
            "aggregate_id": fact_id,
            "event_type": "CREATED",
            "payload": {
                "id": fact_id,
                "entity": "user",
                "attribute": "preferred_editor",
                "value": "VS Code",
                "memory_type": "preference",
                "epistemic_type": "observed_fact",
            },
        },
        {
            "id": str(uuid.uuid4()),
            "aggregate_type": "ENTITY",
            "aggregate_id": entity_id,
            "event_type": "CREATED",
            "payload": {
                "id": entity_id,
                "canonical_name": "FastAPI",
                "category": "framework",
                "aliases": ["fastapi"],
                "description": "Modern async Python web framework.",
            },
        },
        {
            "id": str(uuid.uuid4()),
            "aggregate_type": "TRIPLE",
            "aggregate_id": triple_id,
            "event_type": "CREATED",
            "payload": {
                "id": triple_id,
                "subject_name": "Lumen",
                "predicate": "BUILT_WITH",
                "object_name": "FastAPI",
                "confidence": 0.95,
            },
        },
    ]

    mock_client.list_pending_outbox = AsyncMock(return_value=pending_items)
    mock_client.ack_outbox_event = AsyncMock(return_value=True)
    mock_client.fail_outbox_event = AsyncMock(return_value=True)

    worker = OutboxWorker(
        memory_client=mock_client,
        vault_sync=temp_vault,
        fallback_poll_interval=1.0,
    )

    synced_count = await worker.process_pending_batch()

    assert synced_count == 3
    assert worker.processed_count == 3
    assert worker.failed_count == 0
    assert mock_client.ack_outbox_event.call_count == 3

    # Check generated files in vault
    stats = temp_vault.get_vault_stats()
    assert stats["memories"] >= 1
    assert stats["entities"] >= 2  # FastAPI and Lumen (auto-created via triple)


@pytest.mark.asyncio
async def test_outbox_worker_handles_failures_gracefully(temp_vault: ObsidianVaultSync):
    """Verify that a corrupted payload or disk error triggers failure reporting without crashing worker."""
    mock_client = MagicMock()
    failed_item_id = str(uuid.uuid4())

    bad_items = [
        {
            "id": failed_item_id,
            "aggregate_type": "FACT",
            "aggregate_id": str(uuid.uuid4()),
            "event_type": "CREATED",
            "payload": None,  # Will cause AttributeError when accessed
        }
    ]

    mock_client.list_pending_outbox = AsyncMock(return_value=bad_items)
    mock_client.ack_outbox_event = AsyncMock(return_value=True)
    mock_client.fail_outbox_event = AsyncMock(return_value=True)

    worker = OutboxWorker(
        memory_client=mock_client,
        vault_sync=temp_vault,
    )

    synced_count = await worker.process_pending_batch()

    assert synced_count == 0
    assert worker.processed_count == 0
    assert worker.failed_count == 1
    mock_client.fail_outbox_event.assert_called_once()
    assert failed_item_id in mock_client.fail_outbox_event.call_args[0][0]


@pytest.mark.asyncio
async def test_live_outbox_worker_sync_flow(temp_vault: ObsidianVaultSync):
    """End-to-end integration test against live Memory Engine and PostgreSQL."""
    client = MemoryServiceClient(base_url="http://localhost:8000")
    unique = uuid.uuid4().hex[:6]

    # 1. Propose triple via client
    res = await client.propose_triple(
        subject=f"System_{unique}",
        predicate="USES",
        object_=f"Memory_{unique}",
        confidence=1.0,
    )
    assert res is not None

    # 2. Run OutboxWorker drain pass until all pending are processed
    worker = OutboxWorker(
        memory_client=client,
        vault_sync=temp_vault,
    )
    synced = 0
    for _ in range(5):
        batch_synced = await worker.process_pending_batch()
        synced += batch_synced
        if (temp_vault.entities_dir / f"System_{unique}.md").exists():
            break
    assert synced >= 1

    # 3. Check entity note exists on disk
    sub_file = temp_vault.entities_dir / f"System_{unique}.md"
    assert sub_file.exists()
    content = sub_file.read_text(encoding="utf-8")
    assert f"[[Memory_{unique}]]" in content
    assert "[[USES]]" in content

    await client.close()

