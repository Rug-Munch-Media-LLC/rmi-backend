"""Prometheus metrics - /metrics endpoint + PrometheusMiddleware.

Per RMIV5 v4.0 §T32. Exposes:
  - /metrics                  Prometheus scrape target
  - PrometheusMiddleware      Per-request metrics (count, latency, errors)
  - DataBus cache hit/miss    Per-cache-type metrics

The factory expects `router` attribute (mounted at app root).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import REGISTRY, Counter, Gauge, Histogram, generate_latest

# ── Metrics definitions ──────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "rmi_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "rmi_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
)
ERROR_COUNT = Counter(
    "rmi_http_errors_total",
    "Total HTTP errors",
    ["method", "endpoint", "error_type"],
)
DATABUS_CACHE_HITS = Counter(
    "rmi_databus_cache_hits_total",
    "DataBus cache hits",
    ["data_type", "cache_level"],
)
DATABUS_CACHE_MISSES = Counter(
    "rmi_databus_cache_misses_total",
    "DataBus cache misses",
    ["data_type"],
)
ACTIVE_REQUESTS = Gauge(
    "rmi_http_requests_active",
    "Currently active requests",
)
HEALTH_CHECK_DURATION = Gauge(
    "rmi_health_check_duration_seconds",
    "Last health-check latency per store",
    ["store"],
)
HEALTH_CHECK_STATUS = Gauge(
    "rmi_health_check_status",
    "Last health-check status per store (1=healthy, 0=unhealthy)",
    ["store"],
)


# ── PrometheusMiddleware ────────────────────────────────────────────
class PrometheusMiddleware:
    """Per-request Prometheus metrics.

    Tracks count, latency, errors, and active requests. Compatible
    with starlette's middleware protocol (no inheritance needed
    because we use the @app.middleware('http') decorator pattern
    in lifespan/middleware_setup).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        ACTIVE_REQUESTS.inc()
        start = time.perf_counter()
        status_holder: dict[str, int] = {"code": 500}

        async def wrapped_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            elapsed = time.perf_counter() - start
            ACTIVE_REQUESTS.dec()
            endpoint = scope.get("path", "")
            method = scope.get("method", "")
            status = str(status_holder["code"])
            REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status).inc()
            REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(elapsed)
            if status_holder["code"] >= 400:
                ERROR_COUNT.labels(method=method, endpoint=endpoint, error_type=status).inc()


# ── Router (factory expects this attribute) ─────────────────────────
router = APIRouter(tags=["metrics"], include_in_schema=False)


@router.get("/metrics")
async def metrics_endpoint() -> Response:
    """Prometheus scrape target. Returns text/plain exposition format."""
    return Response(content=generate_latest(REGISTRY), media_type="text/plain")
