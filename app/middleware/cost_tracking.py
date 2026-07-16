"""Cost tracking middleware - per-tenant/per-route cost enforcement.

Per RMIV5 v4.0 §T31. Tracks per-request cost in USD and enforces
budget caps per tenant. Stub implementation: records costs but
doesn't enforce caps yet.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import Request, Response

log = get_logger(__name__)


class CostBuffer:
    """In-memory buffer of recent costs.

    Production version writes to Redis for durability across pods.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        self._buffer: asyncio.Queue[tuple[str, float, float, float]] = asyncio.Queue(
            maxsize=max_size
        )
        self._totals: dict[str, float] = defaultdict(float)

    def record(self, tenant_id: str, route: str, cost_usd: float, latency_s: float) -> None:
        """Record a single cost event."""
        try:  # noqa: SIM105
            self._buffer.put_nowait((tenant_id, cost_usd, latency_s, time.monotonic()))
        except asyncio.QueueFull:
            # Drop oldest if full (acceptable - we keep running totals)
            pass
        self._totals[tenant_id] += cost_usd

    def get_total(self, tenant_id: str) -> float:
        """Get cumulative cost for a tenant."""
        return self._totals.get(tenant_id, 0.0)

    def get_all_totals(self) -> dict[str, float]:
        """Snapshot of all tenant totals."""
        return dict(self._totals)


class CostTrackingMiddleware(BaseHTTPMiddleware):
    """Records per-request cost in the CostBuffer.

    Currently uses a simple heuristic: x402 endpoints cost $0.01-$1.00,
    free endpoints cost $0. Stub does NOT enforce caps yet.
    """

    def __init__(self, app, buffer: CostBuffer) -> None:
        super().__init__(app)
        self.buffer = buffer

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start

        # Simple heuristic for cost estimation
        path = request.url.path
        if path.startswith("/api/v1/x402/"):
            cost_usd = 0.05  # typical x402 tool call
        elif path.startswith("/api/v1/reports/"):
            cost_usd = 5.0  # $5 report
        else:
            cost_usd = 0.0

        # Tenant ID from header (if present)
        tenant_id = request.headers.get("X-Tenant-ID", "anonymous")

        if cost_usd > 0:
            self.buffer.record(tenant_id, path, cost_usd, elapsed)

        # Surface total cost in response header for ops visibility
        response.headers["X-Cost-Total-USD"] = f"{self.buffer.get_total(tenant_id):.4f}"
        return response
