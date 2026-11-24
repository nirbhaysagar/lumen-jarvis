"""
Unit Tests for Memory Client Timeouts and Retries (Milestone M0.9).
Verifies:
1. Granular timeout defaults (connect: 2.0s, read: 15.0s, extract: 120.0s).
2. extract_and_apply explicitly uses extract_timeout (120s).
3. Idempotent retry on capture with idempotency_key.
4. get_save_report endpoint dispatch.
5. Circuit breaker behavior on repeated connection failures.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from src.memory_client.client import (
    MemoryServiceClient,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_EXTRACT_READ_TIMEOUT,
    DEFAULT_TIMEOUT,
    EXTRACT_TIMEOUT,
)


def test_default_timeout_configuration():
    client = MemoryServiceClient(base_url="http://127.0.0.1:8000")
    assert client.timeout.connect == DEFAULT_CONNECT_TIMEOUT
    assert client.timeout.read == DEFAULT_READ_TIMEOUT
    assert client.extract_timeout.connect == DEFAULT_CONNECT_TIMEOUT
    assert client.extract_timeout.read == DEFAULT_EXTRACT_READ_TIMEOUT


def test_custom_timeout_configuration():
    custom_timeout = httpx.Timeout(timeout=10.0, connect=1.0, read=10.0)
    custom_extract = httpx.Timeout(timeout=60.0, connect=1.5, read=60.0)
    client = MemoryServiceClient(
        base_url="http://127.0.0.1:8000",
        timeout=custom_timeout,
        extract_timeout=custom_extract,
    )
    assert client.timeout.read == 10.0
    assert client.timeout.connect == 1.0
    assert client.extract_timeout.read == 60.0
    assert client.extract_timeout.connect == 1.5


@pytest.mark.asyncio
async def test_extract_and_apply_uses_extract_timeout():
    client = MemoryServiceClient(base_url="http://127.0.0.1:8000")
    
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok", "memories": []}
    
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await client.extract_and_apply(event_id="test-event-123")
        
        assert res == {"status": "ok", "memories": []}
        mock_post.assert_awaited_once()
        _, kwargs = mock_post.call_args
        assert kwargs["timeout"] == client.extract_timeout
        assert kwargs["timeout"].read == 120.0


@pytest.mark.asyncio
async def test_capture_idempotent_retry_on_network_error():
    client = MemoryServiceClient(base_url="http://127.0.0.1:8000")

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"event_id": "evt-123", "status": "captured"}

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        # First attempt fails with ConnectError, second succeeds
        mock_post.side_effect = [
            httpx.ConnectError("connection refused"),
            mock_resp,
        ]

        res = await client.capture(
            content="Hello world",
            idempotency_key="idemp-key-abc",
        )

        assert res == {"event_id": "evt-123", "status": "captured"}
        assert mock_post.await_count == 2
        assert client.circuit_open is False


@pytest.mark.asyncio
async def test_capture_without_idempotency_key_does_not_retry():
    client = MemoryServiceClient(base_url="http://127.0.0.1:8000")

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("connection refused")

        res = await client.capture(
            content="Hello world without key",
            idempotency_key=None,
        )

        assert res is None
        assert mock_post.await_count == 1
        assert client.failure_count == 1


@pytest.mark.asyncio
async def test_get_save_report_endpoint():
    client = MemoryServiceClient(base_url="http://127.0.0.1:8000")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "event_id": "evt-abc",
        "stage_ms": {"extract": 10, "verify": 2, "resolve": 3, "commit": 5},
        "counts": {"created": 2},
        "memories": [{"id": "m-1"}],
    }

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        report = await client.get_save_report("evt-abc")

        assert report is not None
        assert report["event_id"] == "evt-abc"
        assert report["counts"]["created"] == 2
        mock_get.assert_awaited_once_with(
            "http://127.0.0.1:8000/v1/events/evt-abc/save-report",
            headers={},
        )


@pytest.mark.asyncio
async def test_circuit_breaker_trips_after_max_failures():
    client = MemoryServiceClient(base_url="http://127.0.0.1:8000", max_consecutive_failures=3)

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("down")

        for _ in range(3):
            await client.recall(query="test")

        assert client.circuit_open is True

        # When circuit is open, subsequent calls immediately return without making network requests
        mock_post.reset_mock()
        result = await client.recall(query="test")
        assert result == []
        mock_post.assert_not_called()
