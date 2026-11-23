"""
Unit Tests for Memory Client Abstention Handling (Milestone M0.7).
Verifies that when the memory engine returns status='abstain', the client
returns [] and sets last_abstained=True, and format_context_with_memories
injects the honest abstention note.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.memory_client.client import MemoryServiceClient
from src.persona.jarvis import format_context_with_memories


@pytest.mark.asyncio
async def test_client_recall_abstention():
    client = MemoryServiceClient(base_url="http://127.0.0.1:8000")
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "abstain",
        "results": [],
        "abstain_reason": "no_candidates",
        "confidence": None,
        "trace_id": "00000000-0000-0000-0000-000000000000",
        "count": 0,
    }
    
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        results = await client.recall(query="What is my blood group?")
        
        assert results == []
        assert getattr(client, "last_abstained", False) is True


@pytest.mark.asyncio
async def test_search_uses_recall_without_legacy_fallback():
    client = MemoryServiceClient(base_url="http://127.0.0.1:8000")
    
    with patch.object(client, "recall", new_callable=AsyncMock) as mock_recall:
        mock_recall.return_value = []
        results = await client.search(query="Anything")
        assert results == []
        mock_recall.assert_awaited_once_with(query="Anything", limit=5, trace_id=None)


def test_format_context_with_memories_abstention_note():
    messages = format_context_with_memories(
        memories=[],
        conversation_history=[],
        abstained=True,
    )
    system_msg = messages[0]["content"]
    assert "I have no record of that, Sir" in system_msg
