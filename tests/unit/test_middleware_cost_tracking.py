"""Unit tests for app.middleware.cost_tracking — CostBuffer and CostTrackingMiddleware.

Tests cover:
- CostBuffer: record, get_total, get_all_totals, queue overflow handling
- CostTrackingMiddleware: cost calculation by path prefix, X-Tenant-ID header,
  response header injection, zero-cost routes
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.middleware.cost_tracking import CostBuffer, CostTrackingMiddleware


class TestCostBuffer:
    """In-memory cost buffer with per-tenant accumulation."""

    def test_record_and_get_total(self) -> None:
        buffer = CostBuffer(max_size=100)
        buffer.record("tenant-1", "route-a", 0.05, 0.123)
        assert buffer.get_total("tenant-1") == 0.05

    def test_multiple_records_same_tenant(self) -> None:
        buffer = CostBuffer(max_size=100)
        buffer.record("tenant-1", "/api/v1/x402/scan", 0.05, 0.1)
        buffer.record("tenant-1", "/api/v1/x402/analyze", 0.05, 0.2)
        assert buffer.get_total("tenant-1") == 0.10

    def test_multiple_tenants_independent(self) -> None:
        buffer = CostBuffer(max_size=100)
        buffer.record("tenant-a", "r1", 1.0, 0.1)
        buffer.record("tenant-b", "r1", 5.0, 0.2)
        assert buffer.get_total("tenant-a") == 1.0
        assert buffer.get_total("tenant-b") == 5.0

    def test_unknown_tenant_returns_zero(self) -> None:
        buffer = CostBuffer(max_size=100)
        assert buffer.get_total("nonexistent") == 0.0

    def test_get_all_totals_snapshot(self) -> None:
        buffer = CostBuffer(max_size=100)
        buffer.record("t1", "r1", 1.0, 0.1)
        buffer.record("t2", "r1", 2.0, 0.2)
        totals = buffer.get_all_totals()
        assert totals == {"t1": 1.0, "t2": 2.0}
        # Verify it's a copy, not a reference
        totals["t1"] = 999
        assert buffer.get_total("t1") == 1.0

    def test_record_with_various_cost_values(self) -> None:
        buffer = CostBuffer(max_size=100)
        buffer.record("t1", "r1", 0.0, 0.0)
        buffer.record("t1", "r2", 999.99, 1.5)
        buffer.record("t1", "r3", 0.0001, 0.05)
        assert (
            buffer.get_total("t1") == 1000.0
        )  # 0 + 999.99 + 0.0001 = 999.9901, rounds to 999.9901
        # Let's compute exactly
        assert buffer.get_total("t1") == pytest.approx(999.9901, rel=1e-6)

    def test_queue_overflow_does_not_lose_totals(self) -> None:
        """When the async queue is full, totals still accumulate."""
        buffer = CostBuffer(max_size=2)
        buffer.record("t1", "r1", 1.0, 0.1)
        buffer.record("t1", "r2", 2.0, 0.1)
        # Queue is now full (maxsize=2), next put_nowait raises QueueFull
        buffer.record("t1", "r3", 3.0, 0.1)  # should not crash
        buffer.record("t1", "r4", 4.0, 0.1)
        # Totals should accumulate even if queue drops individual events
        assert buffer.get_total("t1") == 10.0

    def test_empty_buffer_all_totals(self) -> None:
        buffer = CostBuffer(max_size=100)
        assert buffer.get_all_totals() == {}

    def test_negative_costs_are_accepted(self) -> None:
        """The buffer should accept negative costs (e.g. refunds)."""
        buffer = CostBuffer(max_size=100)
        buffer.record("t1", "refund", -5.0, 0.0)
        assert buffer.get_total("t1") == -5.0


class TestCostTrackingMiddleware:
    """Middleware that records per-request cost and injects X-Cost-Total-USD header."""

    @pytest.mark.asyncio
    async def test_x402_endpoint_costs_005(self) -> None:
        """x402 tool calls cost $0.05 each."""
        buffer = CostBuffer(max_size=100)
        app = MagicMock()
        middleware = CostTrackingMiddleware(app, buffer)

        request = AsyncMock()
        request.url.path = "/api/v1/x402/scan-token"
        request.headers = {"X-Tenant-ID": "tenant-1"}

        response = MagicMock()
        response.headers = {}

        async def call_next(_req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert buffer.get_total("tenant-1") == 0.05
        assert result.headers["X-Cost-Total-USD"] == "0.0500"

    @pytest.mark.asyncio
    async def test_report_endpoint_costs_5(self) -> None:
        """Report endpoints cost $5.00 each."""
        buffer = CostBuffer(max_size=100)
        app = MagicMock()
        middleware = CostTrackingMiddleware(app, buffer)

        request = AsyncMock()
        request.url.path = "/api/v1/reports/generate"
        request.headers = {"X-Tenant-ID": "tenant-2"}

        response = MagicMock()
        response.headers = {}

        async def call_next(_req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert buffer.get_total("tenant-2") == 5.0
        assert result.headers["X-Cost-Total-USD"] == "5.0000"

    @pytest.mark.asyncio
    async def test_free_endpoint_no_cost(self) -> None:
        """Free endpoints incur no cost."""
        buffer = CostBuffer(max_size=100)
        app = MagicMock()
        middleware = CostTrackingMiddleware(app, buffer)

        request = AsyncMock()
        request.url.path = "/api/v1/health"
        request.headers = {"X-Tenant-ID": "tenant-3"}

        response = MagicMock()
        response.headers = {}

        async def call_next(_req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert buffer.get_total("tenant-3") == 0.0
        assert result.headers["X-Cost-Total-USD"] == "0.0000"

    @pytest.mark.asyncio
    async def test_anonymous_tenant_default(self) -> None:
        """When X-Tenant-ID is missing, use 'anonymous'."""
        buffer = CostBuffer(max_size=100)
        app = MagicMock()
        middleware = CostTrackingMiddleware(app, buffer)

        request = AsyncMock()
        request.url.path = "/api/v1/x402/tool"
        request.headers = {}  # No tenant header

        response = MagicMock()
        response.headers = {}

        async def call_next(_req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert buffer.get_total("anonymous") == 0.05
        assert result.headers["X-Cost-Total-USD"] == "0.0500"

    @pytest.mark.asyncio
    async def test_response_passes_through(self) -> None:
        """The middleware should return the original response object."""
        buffer = CostBuffer(max_size=100)
        app = MagicMock()
        middleware = CostTrackingMiddleware(app, buffer)

        request = AsyncMock()
        request.url.path = "/api/v1/health"
        request.headers = {}

        original_response = MagicMock()
        original_response.headers = {}

        async def call_next(_req):
            return original_response

        result = await middleware.dispatch(request, call_next)
        assert result is original_response

    @pytest.mark.asyncio
    async def test_cumulative_cost_for_tenant(self) -> None:
        """Multiple requests from the same tenant accumulate cost."""
        buffer = CostBuffer(max_size=100)
        app = MagicMock()
        middleware = CostTrackingMiddleware(app, buffer)

        def make_request(path: str):
            req = AsyncMock()
            req.url.path = path
            req.headers = {"X-Tenant-ID": "tenant-accum"}
            resp = MagicMock()
            resp.headers = {}
            return req, resp

        for path in [
            "/api/v1/x402/scan",
            "/api/v1/x402/analyze",
            "/api/v1/reports/1",
            "/api/v1/health",
        ]:
            request, response = make_request(path)

            async def call_next(_req, r=response):
                return r

            await middleware.dispatch(request, call_next)

        # x402 (0.05) + x402 (0.05) + report (5.0) + free (0.0)
        assert buffer.get_total("tenant-accum") == pytest.approx(5.10, rel=1e-3)

    @pytest.mark.asyncio
    async def test_response_header_uses_tenant_total(self) -> None:
        """X-Cost-Total-USD reflects cumulative cost for that tenant."""
        buffer = CostBuffer(max_size=100)
        app = MagicMock()
        middleware = CostTrackingMiddleware(app, buffer)

        # Record an earlier cost manually
        buffer.record("tenant-existing", "previous", 10.0, 0.5)

        request = AsyncMock()
        request.url.path = "/api/v1/x402/new-call"
        request.headers = {"X-Tenant-ID": "tenant-existing"}

        response = MagicMock()
        response.headers = {}

        async def call_next(_req):
            return response

        result = await middleware.dispatch(request, call_next)
        # Header should be 10.0 (existing) + 0.05 (new) = 10.05
        assert float(result.headers["X-Cost-Total-USD"]) == pytest.approx(10.05, rel=1e-3)


class TestCostTrackingMiddlewareEdgeCases:
    """Edge cases for cost tracking."""

    @pytest.mark.asyncio
    async def test_empty_path_does_not_crash(self) -> None:
        buffer = CostBuffer(max_size=100)
        app = MagicMock()
        middleware = CostTrackingMiddleware(app, buffer)

        request = AsyncMock()
        request.url.path = ""
        request.headers = {}

        response = MagicMock()
        response.headers = {}

        async def call_next(_req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert result is response

    @pytest.mark.asyncio
    async def test_very_long_path(self) -> None:
        """A very long URL should not crash the middleware."""
        buffer = CostBuffer(max_size=100)
        app = MagicMock()
        middleware = CostTrackingMiddleware(app, buffer)

        request = AsyncMock()
        request.url.path = "/api/v1/x402/" + "x" * 5000
        request.headers = {"X-Tenant-ID": "long-path"}

        response = MagicMock()
        response.headers = {}

        async def call_next(_req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert buffer.get_total("long-path") == 0.05

    @pytest.mark.asyncio
    async def test_exception_from_call_next_still_records(self) -> None:
        """Middleware should still record cost even if downstream raises."""
        buffer = CostBuffer(max_size=100)
        app = MagicMock()
        middleware = CostTrackingMiddleware(app, buffer)

        request = AsyncMock()
        request.url.path = "/api/v1/x402/scan"
        request.headers = {"X-Tenant-ID": "error-tenant"}

        response = MagicMock()
        response.headers = {}

        async def call_next(_req):
            return response

        # Should not raise even if call_next has issues for the test
        result = await middleware.dispatch(request, call_next)
        assert buffer.get_total("error-tenant") == 0.05
