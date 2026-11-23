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
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        max_consecutive_failures: int = 3,
    ):
        import os
        if base_url is None:
            base_url = os.getenv("LUMEN_MEMORY_URL") or os.getenv("MEMORY_ENGINE_URL", "http://127.0.0.1:8000")
        self.base_url = base_url.rstrip("/")
        self.token = token or os.getenv("LUMEN_MEMORY_TOKEN")
        self.max_failures = max_consecutive_failures
        self.failure_count = 0
        self.circuit_open = False
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.AsyncClient(timeout=4.0, headers=headers)

    def _record_success(self):
        self.failure_count = 0
        self.circuit_open = False

    def _record_failure(self):
        self.failure_count += 1
        if self.failure_count >= self.max_failures:
            self.circuit_open = True
            logger.warning("Memory Service circuit breaker TRIPPED! Falling back to offline mode.")

    async def recall(
        self,
        query: str,
        context: str = "default",
        limit: int = 5,
        threshold: float = 0.01,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """5-channel hybrid recall (vector, lexical, entity, graph, temporal) via POST /v1/recall."""
        if self.circuit_open:
            return []

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/recall",
                headers=headers,
                json={"query": query, "context": context, "limit": limit, "threshold": threshold},
            )
            if resp.status_code == 200:
                self._record_success()
                data = resp.json()
                if data.get("status") == "abstain":
                    self.last_abstained = True
                    return []
                self.last_abstained = False
                return data.get("results", [])
            else:
                self._record_failure()
                return []
        except Exception as e:
            logger.debug(f"Memory service recall error: {e}")
            self._record_failure()
            return []

    async def capture(
        self,
        content: str,
        source: dict[str, Any] | None = None,
        context: str = "default",
        occurred_at: str | None = None,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Synchronously captures raw text into the L0 event log (POST /v1/capture)."""
        if self.circuit_open:
            return None

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        payload = {
            "content": content,
            "source": source or {"kind": "chat", "title": "chat utterance", "author": "user"},
            "context": context,
            "occurred_at": occurred_at,
            "idempotency_key": idempotency_key,
        }
        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/capture",
                headers=headers,
                json=payload,
            )
            if resp.status_code in (200, 201):
                self._record_success()
                return resp.json()
            else:
                self._record_failure()
                return None
        except Exception as e:
            logger.debug(f"Memory service capture error: {e}")
            self._record_failure()
            return None

    async def extract_and_apply(
        self,
        event_id: str,
        content: str | None = None,
        speaker: str = "user",
        context: str = "default",
        trace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Extracts structured memories with verified character-span citations (POST /v1/extract)."""
        if self.circuit_open:
            return None

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        payload = {
            "event_id": event_id,
            "content": content,
            "speaker": speaker,
            "context": context,
        }
        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/extract",
                headers=headers,
                json=payload,
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            else:
                self._record_failure()
                return None
        except Exception as e:
            logger.debug(f"Memory service extract error: {e}")
            self._record_failure()
            return None

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

    async def search(
        self,
        query: str,
        limit: int = 5,
        mode: str = "hybrid",
        k_rrf: int = 60,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Unified Hybrid Retrieval Service.
        Executes 5-channel hybrid recall (/v1/recall).
        """
        if self.circuit_open:
            return []

        return await self.recall(query=query, limit=limit, trace_id=trace_id)

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

    async def propose_triple(
        self,
        subject: str,
        predicate: str,
        object_: str,
        confidence: float = 1.0,
        source_event_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Submit a candidate triple assertion through the Review Queue resolver."""
        if self.circuit_open:
            return None

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v1/graph/propose",
                headers=headers,
                json={
                    "subject": subject,
                    "predicate": predicate,
                    "object": object_,
                    "confidence": confidence,
                    "source_event_id": source_event_id,
                },
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            return None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return None

    async def list_review_queue(
        self,
        status: str | None = "PENDING",
        tier: str | None = None,
        limit: int = 50,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch pending proposals from the review queue."""
        if self.circuit_open:
            return []

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        params = {"limit": limit}
        if status:
            params["status"] = status
        if tier:
            params["tier"] = tier

        try:
            resp = await self._client.get(
                f"{self.base_url}/api/v1/graph/review-queue",
                headers=headers,
                params=params,
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            return []
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return []

    async def resolve_review_proposal(
        self,
        proposal_id: str,
        decision: str,
        trace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Approve or reject a staged proposal in the review queue."""
        if self.circuit_open:
            return None

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v1/graph/review-queue/{proposal_id}/resolve",
                headers=headers,
                json={"decision": decision},
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            return None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return None

    async def traverse_graph(
        self,
        entity_name_or_id: str,
        max_depth: int = 2,
        trace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Traverse connected entities and triples starting from an entity."""
        if self.circuit_open:
            return None

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.get(
                f"{self.base_url}/api/v1/graph/traverse/{entity_name_or_id}",
                headers=headers,
                params={"max_depth": max_depth},
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            return None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return None

    async def list_pending_outbox(
        self,
        limit: int = 50,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch pending outbox records waiting for vault sync."""
        if self.circuit_open:
            return []

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.get(
                f"{self.base_url}/api/v1/outbox/pending",
                headers=headers,
                params={"limit": limit},
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json().get("items", [])
            return []
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return []

    async def ack_outbox_event(
        self,
        outbox_id: str,
        trace_id: str | None = None,
    ) -> bool:
        """Acknowledge an outbox event as successfully processed."""
        if self.circuit_open:
            return False

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v1/outbox/{outbox_id}/ack",
                headers=headers,
            )
            if resp.status_code == 200:
                self._record_success()
                return True
            return False
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return False

    async def fail_outbox_event(
        self,
        outbox_id: str,
        error: str,
        trace_id: str | None = None,
    ) -> bool:
        """Mark an outbox event as failed with error details."""
        if self.circuit_open:
            return False

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v1/outbox/{outbox_id}/fail",
                headers=headers,
                json={"error": error},
            )
            if resp.status_code == 200:
                self._record_success()
                return True
            return False
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return False

    async def get_outbox_stats(
        self,
        trace_id: str | None = None,
    ) -> dict[str, int] | None:
        """Fetch transactional outbox queue statistics."""
        if self.circuit_open:
            return None

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.get(
                f"{self.base_url}/api/v1/outbox/stats",
                headers=headers,
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            return None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return None

    async def run_consolidation(
        self,
        trace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Trigger autonomous knowledge graph consolidation patrol."""
        if self.circuit_open:
            return None

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/v1/graph/consolidate",
                headers=headers,
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            return None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return None

    async def get_orphan_entities(
        self,
        max_degree: int = 1,
        trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch orphan entity nodes."""
        if self.circuit_open:
            return []

        headers = {"X-Trace-ID": trace_id} if trace_id else {}
        try:
            resp = await self._client.get(
                f"{self.base_url}/api/v1/graph/orphans",
                headers=headers,
                params={"max_degree": max_degree},
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json()
            return []
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._record_failure()
            return []

    async def close(self):
        await self._client.aclose()


# Backward compatibility alias
MemoryClient = MemoryServiceClient

