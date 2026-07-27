"""Observability — OpenTelemetry tracing, Langfuse LLM tracing, metrics, error tracking.

Modules:
    tracing         — OpenTelemetry auto-instrumentation setup (FastAPI, SQLAlchemy, HTTP, Redis, logging)
    spans           — Manual span helpers for business-logic instrumentation
    langfuse_tracer — Langfuse integration for LLM observability
    error_tracking  — GlitchTip / Sentry-compatible error tracking (DSN-opt-in)
"""

from .error_tracking import capture_exception, capture_message, flush, init_error_tracking
from .langfuse_tracer import get_langfuse, trace_agent_call
from .spans import async_span, span, traced
from .tracing import get_tracer, init_tracing

__all__ = [
    "async_span",
    "capture_exception",
    "capture_message",
    "flush",
    "get_langfuse",
    "get_tracer",
    "init_error_tracking",
    "init_tracing",
    "span",
    "trace_agent_call",
    "traced",
]
