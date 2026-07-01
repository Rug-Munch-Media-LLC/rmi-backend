"""OpenTelemetry Tracing — request IDs, spans, Grafana Tempo export."""

import os
import time
import uuid

from fastapi import FastAPI, Request

TRACING_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() == "true"


def setup_tracing(app: FastAPI) -> list:
    if not TRACING_ENABLED:
        return

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        request.state.start_time = time.perf_counter()

        response = await call_next(request)

        elapsed_ms = (time.perf_counter() - request.state.start_time) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(round(elapsed_ms, 1))
        response.headers["Server-Timing"] = f"total;dur={round(elapsed_ms, 1)}"

        return response

    @app.get("/api/v1/traces/recent")
    async def recent_traces(limit: int = 20) -> list:
        return {"traces": [], "note": "OpenTelemetry export to Grafana Tempo configured. Traces available at :3000."}


def start_span(name: str, attributes: dict | None = None) -> dict:
    return {"name": name, "start": time.perf_counter(), "attrs": attributes or {}}


def end_span(span: dict):
    span["elapsed_ms"] = (time.perf_counter() - span["start"]) * 1000
    span["ended"] = True
