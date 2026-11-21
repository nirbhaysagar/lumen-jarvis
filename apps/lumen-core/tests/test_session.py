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
    assert len(full_response) > 0

    await unreachable_client.close()


@pytest.mark.asyncio
async def test_session_proposals_and_graph_commands():
    """Verify session /proposals and /graph traversal commands."""
    memory_client = MemoryServiceClient(base_url="http://localhost:8000")
    session = ConversationSession(memory_client=memory_client)

    # 1. Check /proposals command
    tokens = []
    async for token in session.process_turn("/proposals"):
        tokens.append(token)
    reply = "".join(tokens)
    assert ("Review Queue" in reply) or ("Staged Memory Proposals" in reply)

    # 2. Check /graph command for User
    graph_tokens = []
    async for token in session.process_turn("/graph User"):
        graph_tokens.append(token)
    graph_reply = "".join(graph_tokens)
    assert ("Knowledge Graph" in graph_reply) or ("No active graph relationships" in graph_reply)

    # 3. Check /outbox command
    outbox_tokens = []
    async for token in session.process_turn("/outbox"):
        outbox_tokens.append(token)
    outbox_reply = "".join(outbox_tokens)
    assert "Transactional Outbox" in outbox_reply
    assert "Pending Sync Queue" in outbox_reply

    # 4. Check /consolidate command
    consolidate_tokens = []
    async for token in session.process_turn("/consolidate"):
        consolidate_tokens.append(token)
    consolidate_reply = "".join(consolidate_tokens)
    assert "Consolidation Patrol Completed" in consolidate_reply
    assert "Morning Intelligence Digest" in consolidate_reply

    # 5. Check /digest command
    digest_tokens = []
    async for token in session.process_turn("/digest"):
        digest_tokens.append(token)
    digest_reply = "".join(digest_tokens)
    assert "Intelligence Briefing" in digest_reply

    # 6. Check /search command (Hybrid RRF)
    search_tokens = []
    async for token in session.process_turn("/search postgres"):
        search_tokens.append(token)
    search_reply = "".join(search_tokens)
    assert ("Ranked Hybrid Search Results" in search_reply) or ("No relevant memories found" in search_reply)

    # 7. Check /research command dispatching
    research_tokens = []
    async for token in session.process_turn("/research PostgreSQL vector performance"):
        research_tokens.append(token)
    research_reply = "".join(research_tokens)
    assert "The Eyes" in research_reply
    assert "Dispatched autonomous deep research task" in research_reply

    # 8. Check /dossiers command
    dossier_tokens = []
    async for token in session.process_turn("/dossiers"):
        dossier_tokens.append(token)
    dossier_reply = "".join(dossier_tokens)
    assert ("Obsidian Research Dossiers" in dossier_reply) or ("No research dossiers have been generated yet" in dossier_reply)

    await memory_client.close()


@pytest.mark.asyncio
async def test_session_conversational_approval_and_rejection():
    """Verify natural language approvals ('yes, proceed', 'no, cancel') work without slash commands or token IDs."""
    session = ConversationSession()

    # Stage a pending approval
    req = session.approvals.request_approval("echo 'sandbox test'", "Automated test")
    assert req.approval_id in session.approvals._pending

    # 1. Natural confirmation: "yes, proceed"
    tokens = []
    async for tok in session.process_turn("yes, proceed"):
        tokens.append(tok)
    res_text = "".join(tokens)
    assert "Authorization confirmed" in res_text
    assert req.approval_id not in session.approvals._pending

    # Stage another pending approval
    req2 = session.approvals.request_approval("touch /tmp/dummy", "Test rejection")
    assert req2.approval_id in session.approvals._pending

    # 2. Natural rejection: "no, cancel"
    tokens2 = []
    async for tok in session.process_turn("no, cancel"):
        tokens2.append(tok)
    res_text2 = "".join(tokens2)
    assert "cancelled by operator" in res_text2
    assert req2.approval_id not in session.approvals._pending

