"""
Pytest configuration and fixtures for Lumen Memory Engine.
Guarantees strict isolation: all tests run against lumen_memory_test
and use isolated temporary vaults.
"""
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

load_dotenv()

_TEST_TOKEN = secrets.token_urlsafe(32)
_TEST_PG_PASS = os.getenv("POSTGRES_PASSWORD", "")

# Force test environment before importing application modules
os.environ["LUMEN_ENV"] = "test"
os.environ["POSTGRES_DB"] = "lumen_memory_test"
os.environ["POSTGRES_PASSWORD"] = _TEST_PG_PASS
os.environ["LUMEN_DATABASE_URL"] = f"postgresql://lumen:{_TEST_PG_PASS}@localhost:5432/lumen_memory_test"
os.environ["DATABASE_URL"] = f"postgresql://lumen:{_TEST_PG_PASS}@localhost:5432/lumen_memory_test"
os.environ["LUMEN_MEMORY_TOKEN"] = _TEST_TOKEN

from app.core.config import settings
from app.main import app

# Verify settings are pointed to test database and use dynamic token
settings.POSTGRES_DB = "lumen_memory_test"
settings.POSTGRES_PASSWORD = _TEST_PG_PASS
settings.LUMEN_ENV = "test"
settings.LUMEN_MEMORY_TOKEN = _TEST_TOKEN


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
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(
            """
            TRUNCATE TABLE 
                sources, events, chunks, entities, entity_aliases, 
                memories, claims, citations, memory_relations, 
                abstractions, abstraction_sources, memory_signals, 
                retrieval_traces, review_queue, extraction_attempts, outbox, jobs,
                facts, triples, predicates, memory_outbox
            CASCADE;
            """
        )
        # Re-seed canonical predicates required by Knowledge Graph
        await conn.execute(
            """
            INSERT INTO predicates (id, inverse_predicate, description, is_symmetric) VALUES
                ('FOUNDED', 'FOUNDED_BY', 'Entity founded an organization, company, or project', FALSE),
                ('FOUNDED_BY', 'FOUNDED', 'Organization or project founded by an entity', FALSE),
                ('DEVELOPS', 'DEVELOPED_BY', 'Entity builds, authors, or develops a project or software', FALSE),
                ('DEVELOPED_BY', 'DEVELOPS', 'Software or project developed by an entity', FALSE),
                ('PREFERS', NULL, 'User or entity prefers a tool, workflow, or environment', FALSE),
                ('DISLIKES', NULL, 'User or entity explicitly dislikes or rejects an option', FALSE),
                ('USES', 'USED_BY', 'Entity uses or employs a technology, library, or dependency', FALSE),
                ('USED_BY', 'USES', 'Technology used by a system or project', FALSE),
                ('DEPENDS_ON', 'DEPENDENCY_OF', 'Project or component depends on another system', FALSE),
                ('DEPENDENCY_OF', 'DEPENDS_ON', 'Component is a dependency of another system', FALSE),
                ('CONTAINS', 'PART_OF', 'Container or system contains a subsystem or element', FALSE),
                ('PART_OF', 'CONTAINS', 'Subsystem is part of a larger container or entity', FALSE),
                ('WRITTEN_IN', NULL, 'Software is implemented in a specific programming language', FALSE),
                ('INSPIRED_BY', NULL, 'Concept or design is modeled after or inspired by an entity', FALSE),
                ('MEMBER_OF', 'HAS_MEMBER', 'Person or entity belongs to an organization or group', FALSE),
                ('HAS_MEMBER', 'MEMBER_OF', 'Group or organization includes an entity', FALSE)
            ON CONFLICT (id) DO NOTHING;
            """
        )
    finally:
        await conn.close()
    yield


@pytest_asyncio.fixture
async def client():
    """Authenticated test client fixture that triggers FastAPI lifespan."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
            yield c
