"""
Memory Service HTTP Client SDK.
Decoupled client implementing Circuit Breaker, Correlation ID propagation, and Graceful Degradation.
"""
from __future__ import annotations
import logging
import httpx
from typing import Any

logger = logging.getLogger("lumen.memory_client")


class CircuitBreakerOpenException(Exception):
    pass


class MemoryServiceClient:
    """
    HTTP client for the standalone Memory Engine service.
    Implements circuit-breaking: if the memory service is unreachable,
    Lumen gracefully degrades to stateless mode rather than crashing.
    """
    def __init__(self, base_url: str = "http://localhost:8000", max_consecutive_failures: int = 3):
        self.base_url = base_url.rstrip("/")
        self.max_failures = max_consecutive_failures
        self.failure_count = 0
        self.circuit_open = False
        self._client = httpx.AsyncClient(timeout=4.0)

    def _record_success(self):
        self.failure_count = 0
        self.circuit_open = False

    def _record_failure(self):
        self.failure_count += 1
        if self.failure_count >= self.max_failures:
            self.circuit_open = True
            logger.warning("Memory Service circuit breaker TRIPPED! Falling back to offline mode.")

    async def search_memories(
        self,
        query: str,
        limit: int = 4,
        threshold: float = 0.05,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search relevant past memories with circuit breaker."""
        if self.circuit_open:
            return []

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v1/memories/search",
                headers=headers,
                json={"query": query, "limit": limit, "threshold": threshold},
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json().get("results", [])
            else:
                self._record_failure()
                return []
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            logger.debug(f"Memory service connection error: {e}")
            self._record_failure()
            return []

    async def save_event(
        self,
        source: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Ingest an immutable raw event receipt."""
        if self.circuit_open:
            return None

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v1/events",
                headers=headers,
                json={
                    "source": source,
                    "payload": payload,
                    "conversation_id": conversation_id,
                    "idempotency_key": idempotency_key,
                },
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            return None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return None

    async def save_fact(
        self,
        entity: str,
        attribute: str,
        value: Any,
        source_event_ids: list[str],
        memory_type: str = "fact",
        epistemic_type: str = "observed_fact",
        trace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Store a canonical fact with provenance back to event IDs."""
        if self.circuit_open:
            return None

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v1/facts",
                headers=headers,
                json={
                    "entity": entity,
                    "attribute": attribute,
                    "value": value,
                    "source_event_ids": source_event_ids,
                    "memory_type": memory_type,
                    "epistemic_type": epistemic_type,
                },
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            return None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return None

    async def get_fact_trace(
        self,
        fact_id: str,
        trace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve full bi-temporal provenance and historical lineage for a fact."""
        if self.circuit_open:
            return None

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.get(
                f"{self.base_url}/api/v1/trace/{fact_id}",
                headers=headers,
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            return None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return None

    async def time_travel_search(
        self,
        query: str,
        as_of: str,
        limit: int = 4,
        threshold: float = 0.01,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query memory as of a historical moment in time."""
        if self.circuit_open:
            return []

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v1/memories/time-travel",
                headers=headers,
                json={
                    "query": query,
                    "as_of": as_of,
                    "limit": limit,
                    "threshold": threshold,
                },
            )
            if resp.status_code == 200:
                self._record_success()
                data = resp.json()
                return data.get("results", [])
            return []
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return []

    async def close(self):
        await self._client.aclose()
