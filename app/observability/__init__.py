"""Observability — OpenTelemetry tracing, Langfuse LLM tracing, metrics.

Modules:
    tracing     — OpenTelemetry auto-instrumentation setup (FastAPI, SQLAlchemy, HTTP, Redis, logging)
    spans       — Manual span helpers for business-logic instrumentation
    langfuse_tracer — Langfuse integration for LLM observability
"""

from .langfuse_tracer import get_langfuse, trace_agent_call
from .spans import async_span, span, traced
from .tracing import get_tracer, init_tracing

__all__ = [
    "async_span",
    "get_langfuse",
    "get_tracer",
    "init_tracing",
    "span",
    "trace_agent_call",
    "traced",
]
