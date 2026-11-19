"""
Two-Man Rule Approval Manager: Ring 2 of Safe Computer Control.
Pauses modifying or caution commands in the AWAITING_APPROVAL state until
explicitly confirmed or rejected by the human operator.
"""
from __future__ import annotations
import asyncio
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class PendingApproval:
    approval_id: str
    command: str
    reason: str
    created_at: float = field(default_factory=time.time)
    approved_event: asyncio.Event = field(default_factory=asyncio.Event)
    rejected_event: asyncio.Event = field(default_factory=asyncio.Event)


class ApprovalManager:
    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}

    def request_approval(self, command: str, reason: str) -> PendingApproval:
        """Registers a new pending approval request with a clean short ID."""
        approval_id = f"appr_{uuid.uuid4().hex[:6]}"
        req = PendingApproval(approval_id=approval_id, command=command, reason=reason)
        self._pending[approval_id] = req
        return req

    def approve(self, approval_id: str) -> bool:
        """Approves a pending command."""
        req = self._pending.get(approval_id)
        if not req:
            return False
        req.approved_event.set()
        return True

    def reject(self, approval_id: str) -> bool:
        """Rejects a pending command."""
        req = self._pending.get(approval_id)
        if not req:
            return False
        req.rejected_event.set()
        return True

    def list_pending(self) -> list[PendingApproval]:
        """Returns all currently waiting approvals."""
        return list(self._pending.values())

    async def wait_for_decision(self, approval_id: str, timeout: float = 60.0) -> bool:
        """
        Asynchronously waits for human decision (/approve or /reject).
        Returns True if approved, False if rejected or timed out.
        """
        req = self._pending.get(approval_id)
        if not req:
            return False

        try:
            # Wait for either approved or rejected event
            done, _ = await asyncio.wait(
                [
                    asyncio.create_task(req.approved_event.wait()),
                    asyncio.create_task(req.rejected_event.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
                timeout=timeout,
            )

            if not done:
                # Timed out
                return False

            return req.approved_event.is_set()

        finally:
            self._pending.pop(approval_id, None)
