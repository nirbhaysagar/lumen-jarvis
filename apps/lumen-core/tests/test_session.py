"""
Automated Integration Tests for Lumen Core Session & Circuit Breaker.
"""
import pytest
from src.runtime.session import ConversationSession
from src.memory_client.client import MemoryServiceClient
from src.router.model_router import ModelRouter


@pytest.mark.asyncio
async def test_conversation_session_turn():
    """Verify end-to-end conversation turn and streaming."""
    memory_client = MemoryServiceClient(base_url="http://localhost:8000")
    router = ModelRouter()
    session = ConversationSession(memory_client=memory_client, router=router)

    tokens = []
    async for token in session.process_turn("Hello Jarvis"):
        tokens.append(token)

    full_response = "".join(tokens)
    assert len(full_response) > 0
    assert "Sir" in full_response
    assert len(session.history) == 2  # user + assistant

    await memory_client.close()


@pytest.mark.asyncio
async def test_circuit_breaker_offline_fallback():
    """Verify circuit breaker trips when memory engine is down and session still operates gracefully."""
    # Point to non-existent port to simulate down service
    unreachable_client = MemoryServiceClient(base_url="http://localhost:9999", max_consecutive_failures=1)
    session = ConversationSession(memory_client=unreachable_client)

    tokens = []
    # Should not crash with connection error!
    async for token in session.process_turn("Status report"):
        tokens.append(token)

    assert unreachable_client.circuit_open is True
    full_response = "".join(tokens)
    assert "Sir" in full_response

    await unreachable_client.close()
