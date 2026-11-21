"""
Pytest configuration for apps/lumen-core.
Ensures test runs isolate all vault file writes to a temporary directory
to prevent polluting the user's live Obsidian vault.
"""
import os
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_vault(tmp_path, monkeypatch):
    """Automatically isolate all vault operations during tests."""
    temp_vault = tmp_path / "vault"
    temp_vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LUMEN_VAULT_DIR", str(temp_vault))
    monkeypatch.setenv("LUMEN_ENV", "test")
    monkeypatch.setenv("POSTGRES_DB", "lumen_memory_test")
    monkeypatch.setenv("LUMEN_DATABASE_URL", "postgresql://lumen:lumen_secure_pass@localhost:5432/lumen_memory_test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://lumen:lumen_secure_pass@localhost:5432/lumen_memory_test")
    yield temp_vault
