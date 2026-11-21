"""
Pytest configuration and fixtures for Lumen Memory Engine.
Guarantees strict isolation: all tests run against lumen_memory_test
and use isolated temporary vaults.
"""
import os
from pathlib import Path
import pytest

# Force test environment before importing application modules
os.environ["LUMEN_ENV"] = "test"
os.environ["POSTGRES_DB"] = "lumen_memory_test"
os.environ["LUMEN_DATABASE_URL"] = "postgresql://lumen:lumen_secure_pass@localhost:5432/lumen_memory_test"
os.environ["DATABASE_URL"] = "postgresql://lumen:lumen_secure_pass@localhost:5432/lumen_memory_test"
os.environ["LUMEN_MEMORY_TOKEN"] = "test-token-secret"

from app.core.config import settings

# Verify settings are pointed to test database
settings.POSTGRES_DB = "lumen_memory_test"
settings.LUMEN_ENV = "test"
settings.LUMEN_MEMORY_TOKEN = "test-token-secret"


@pytest.fixture(autouse=True)
def isolate_vault(tmp_path, monkeypatch):
    """Isolate any vault writes to a temporary directory."""
    temp_vault = tmp_path / "vault"
    temp_vault.mkdir(parents=True, exist_ok=True)
    (temp_vault / "entities").mkdir(parents=True, exist_ok=True)
    (temp_vault / "memories").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LUMEN_VAULT_DIR", str(temp_vault))
    yield temp_vault


@pytest.fixture(autouse=True)
async def clean_test_db():
    """Ensure a pristine test database before each test run."""
    import asyncpg
    conn = await asyncpg.connect(
        "postgresql://lumen:lumen_secure_pass@localhost:5432/lumen_memory_test"
    )
    try:
        await conn.execute(
            """
            TRUNCATE TABLE 
                sources, events, chunks, entities, entity_aliases, 
                memories, claims, citations, memory_relations, 
                abstractions, abstraction_sources, memory_signals, 
                retrieval_traces, review_queue, outbox, jobs 
            CASCADE;
            """
        )
    finally:
        await conn.close()
    yield
