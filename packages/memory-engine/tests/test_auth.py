"""
Unit Tests for Authentication and Security Middleware (Milestone M0.2).
Verifies:
  1. No token -> 401 Unauthorized (fail closed).
  2. Wrong token -> 401 Unauthorized.
  3. Correct token -> 200 OK.
  4. /health endpoint is public without token.
  5. The application lifespan refuses to start without token (>=32 chars) when LUMEN_ENV != 'test'.
"""
import pytest
from httpx import AsyncClient, ASGITransport
from app.core.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_health_endpoint_is_public():
    """Verify /health can be accessed without any authorization header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "healthy"


@pytest.mark.asyncio
async def test_protected_route_missing_token_returns_401():
    """Verify missing Authorization header fails closed with 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/recall", json={"query": "test query"})
        assert resp.status_code == 401
        assert "Missing Bearer token" in resp.json()["detail"] or "Authentication required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_protected_route_invalid_token_returns_401():
    """Verify invalid Authorization header returns 401."""
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer invalid_token_12345678901234567890"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/recall", headers=headers, json={"query": "test query"})
        assert resp.status_code == 401
        assert "Invalid LUMEN_MEMORY_TOKEN" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_protected_route_valid_token_succeeds():
    """Verify valid LUMEN_MEMORY_TOKEN bearer header succeeds."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/recall", headers=headers, json={"query": "test query"})
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_lifespan_refuses_to_start_without_mandatory_token(monkeypatch):
    """Verify that lifespan raises RuntimeError if token is missing or < 32 chars outside test."""
    monkeypatch.setattr(settings, "LUMEN_ENV", "production")
    monkeypatch.setattr(settings, "LUMEN_MEMORY_TOKEN", None)

    with pytest.raises(RuntimeError, match="LUMEN_MEMORY_TOKEN must be set"):
        async with app.router.lifespan_context(app):
            pass

    monkeypatch.setattr(settings, "LUMEN_MEMORY_TOKEN", "too_short")
    with pytest.raises(RuntimeError, match="LUMEN_MEMORY_TOKEN must be set"):
        async with app.router.lifespan_context(app):
            pass
