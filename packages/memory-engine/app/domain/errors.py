"""
Domain-level errors and exceptions for Lumen Memory Engine.
"""
from typing import Any, Optional


class ExtractionError(Exception):
    """Raised when structured memory extraction fails or truncates."""

    def __init__(
        self,
        kind: str,
        detail: str = "",
        raw: str = "",
        attempt: int = 1,
        latency_ms: int = 0,
    ) -> None:
        super().__init__(f"Extraction failed [{kind}]: {detail}")
        self.kind = kind  # 'llm_error' | 'json_decode' | 'schema' | 'truncated'
        self.detail = detail
        self.raw = raw
        self.attempt = attempt
        self.latency_ms = latency_ms


class NotFoundError(Exception):
    """Raised when a requested resource (memory, lineage, directive) does not exist."""
    pass


class ConflictError(Exception):
    """Raised when a concurrent modification or stale version conflict is detected."""
    pass
