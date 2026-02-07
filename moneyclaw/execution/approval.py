"""Human approval gate — for operations above the threshold."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()


@dataclass
class ApprovalRequest:
    id: str
    description: str
    amount: float
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending, approved, rejected, expired
    expires_in: float = 3600  # 1 hour default


class ApprovalGate:
    """Manages pending approval requests. Integrates with Telegram for human review."""

    def __init__(self, expiry: float = 3600) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._expiry = expiry

    def request(self, req_id: str, description: str, amount: float) -> ApprovalRequest:
        """Create a new approval request."""
        req = ApprovalRequest(
            id=req_id,
            description=description,
            amount=amount,
            expires_in=self._expiry,
        )
        self._pending[req_id] = req
        log.info("approval.requested", id=req_id, amount=amount)
        return req

    def approve(self, req_id: str) -> bool:
        req = self._pending.get(req_id)
        if req and req.status == "pending":
            req.status = "approved"
            log.info("approval.approved", id=req_id)
            return True
        return False

    def reject(self, req_id: str) -> bool:
        req = self._pending.get(req_id)
        if req and req.status == "pending":
            req.status = "rejected"
            log.info("approval.rejected", id=req_id)
            return True
        return False

    async def wait_for(self, req_id: str, timeout: float | None = None) -> str:
        """Wait for an approval decision. Returns 'approved', 'rejected', or 'expired'."""
        timeout = timeout or self._expiry
        start = time.monotonic()

        while time.monotonic() - start < timeout:
            req = self._pending.get(req_id)
            if req is None:
                return "expired"
            if req.status != "pending":
                return req.status
            await asyncio.sleep(1)

        # Expired
        req = self._pending.get(req_id)
        if req and req.status == "pending":
            req.status = "expired"
        return "expired"

    def get_pending(self) -> list[ApprovalRequest]:
        self._cleanup_expired()
        return [r for r in self._pending.values() if r.status == "pending"]

    def _cleanup_expired(self) -> None:
        now = time.time()
        for req in list(self._pending.values()):
            if req.status == "pending" and now - req.created_at > req.expires_in:
                req.status = "expired"
