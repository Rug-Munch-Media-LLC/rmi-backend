"""Cost tracking middleware - per-tenant/per-route cost enforcement + timing stats.

Per RMIV5 v4.0 §T31. Tracks per-request cost in USD and enforces
budget caps per tenant. Stub implementation: records costs but
doesn't enforce caps yet.

Also tracks per-endpoint timing statistics for performance monitoring.
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


class TimingStats:
    """Per-endpoint timing histogram for performance monitoring.

    Tracks count, total time, min, max, and average for each endpoint path.
    """

    def __init__(self) -> None:
        self._stats: dict[str, dict[str, float]] = defaultdict(
            lambda: {"count": 0, "total_ms": 0.0, "min_ms": float("inf"), "max_ms": 0.0}
        )

    def record(self, path: str, elapsed_ms: float) -> None:
        """Record a timing sample for an endpoint."""
        s = self._stats[path]
        s["count"] += 1
        s["total_ms"] += elapsed_ms
        if elapsed_ms < s["min_ms"]:
            s["min_ms"] = elapsed_ms
        if elapsed_ms > s["max_ms"]:
            s["max_ms"] = elapsed_ms

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Return snapshot of current timing stats with computed averages."""
        result = {}
        for path, s in self._stats.items():
            result[path] = {
                "count": s["count"],
                "avg_ms": round(s["total_ms"] / s["count"], 1) if s["count"] > 0 else 0,
                "min_ms": round(s["min_ms"], 1) if s["min_ms"] != float("inf") else 0,
                "max_ms": round(s["max_ms"], 1),
                "total_ms": round(s["total_ms"], 1),
            }
        return result

    def clear(self) -> None:
        """Reset all timing stats."""
        self._stats.clear()


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
    """Records per-request cost + timing stats.

    Cost: x402 endpoints cost $0.05, reports $5, free $0.
    Timing: per-endpoint histogram exposed via timing_stats property.
    """

    def __init__(self, app, buffer: CostBuffer) -> None:
        super().__init__(app)
        self.buffer = buffer
        self.timing_stats = TimingStats()

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start

        path = request.url.path
        elapsed_ms = elapsed * 1000

        # Record endpoint timing stats
        self.timing_stats.record(path, elapsed_ms)

        # Log slow endpoints (>1s) for ops visibility
        if elapsed > 1.0:
            log.warning(
                "slow_endpoint path=%s method=%s elapsed_ms=%.0f",
                path,
                request.method,
                elapsed_ms,
            )

        # Simple heuristic for cost estimation
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
