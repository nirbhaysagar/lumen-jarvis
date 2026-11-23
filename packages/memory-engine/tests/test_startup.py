"""
Unit Tests for Application Startup, Embedding Verification, and Local-Only Enforcement (Milestone M0.4).
Verifies:
  1. In test environment (LUMEN_ENV='test'), startup uses DeterministicHashEmbeddingAdapter.
  2. Outside test (production), startup verifies Ollama is reachable and model is pulled.
  3. Outside test, startup fails loudly if Ollama is unreachable.
  4. Outside test, startup fails loudly if the embedding model is missing.
  5. GET /health exposes embedding_model, dimension, and ollama status.
"""
import pytest
import httpx
from httpx import AsyncClient, ASGITransport

from app.core.config import settings
from app.adapters.embeddings.embedding_adapter import DeterministicHashEmbeddingAdapter, OllamaEmbeddingAdapter
from app.main import app


@pytest.mark.asyncio
async def test_startup_in_test_environment_uses_hash_adapter():
    """Verify test environment safely defaults to deterministic hash adapter without hitting network."""
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.embedding, DeterministicHashEmbeddingAdapter)
        assert app.state.embedding.dimension == settings.EMBEDDING_DIMENSION


@pytest.mark.asyncio
async def test_startup_outside_test_fails_if_ollama_unreachable(monkeypatch):
    """Verify production startup fails loudly with clear error when Ollama cannot be reached."""
    monkeypatch.setattr(settings, "LUMEN_ENV", "production")
    monkeypatch.setattr(settings, "LUMEN_MEMORY_TOKEN", "a_valid_32_character_token_for_production_testing")
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://127.0.0.1:54321")  # non-existent port

    with pytest.raises(RuntimeError, match="Ollama embedding validation failed"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_startup_outside_test_fails_if_model_not_pulled(monkeypatch):
    """Verify production startup fails if the requested embedding model is not in Ollama /api/tags."""
    monkeypatch.setattr(settings, "LUMEN_ENV", "production")
    monkeypatch.setattr(settings, "LUMEN_MEMORY_TOKEN", "a_valid_32_character_token_for_production_testing")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "non_existent_embedding_model_xyz")

    async def mock_handler(request: httpx.Request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b-instruct"}]})
        return httpx.Response(404)

    # Monkeypatch httpx.AsyncClient inside main's lifespan
    real_async_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(mock_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("httpx.AsyncClient", mock_client_factory)

    with pytest.raises(RuntimeError, match="is not pulled in Ollama"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_health_check_returns_embedding_metadata():
    """Verify /health returns embedding_model, dim, and ollama fields."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["embedding_model"] == settings.EMBEDDING_MODEL
        assert data["dim"] == settings.EMBEDDING_DIMENSION
        assert data["ollama"] == "ok"
