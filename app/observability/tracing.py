"""
OpenTelemetry distributed tracing setup.

Auto-instruments FastAPI, SQLAlchemy, HTTP/HTTPS requests, Redis, and logging.
Adds manual spans for business-logic visibility.

Usage:
    from app.observability.tracing import init_tracing

    init_tracing(service_name="rmi-backend")

Environment variables:
    OTEL_EXPORTER_OTLP_ENDPOINT — OTLP HTTP endpoint (default: http://localhost:4318/v1/traces)
    OTEL_DISABLED               — set any truthy value to disable tracing entirely
    PYTEST_CURRENT_TEST         — auto-detected, tracing skipped in pytest
    APP_VERSION                 — service version for span metadata
    ENV                         — deployment environment tag (default: development)
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.logging import get_logger

logger = get_logger(__name__)

_tracing_initialized = False


def init_tracing(
    service_name: str = "rmi-backend",
    otlp_endpoint: str | None = None,
) -> None:
    """Initialize OpenTelemetry distributed tracing.

    Idempotent — safe to call multiple times (only runs once).
    Opt-in: disabled by default in test environments or when ``OTEL_DISABLED`` is set.
    """
    global _tracing_initialized

    if _tracing_initialized:
        return

    # Skip in test environments or when explicitly disabled
    if os.environ.get("OTEL_DISABLED") or os.environ.get("PYTEST_CURRENT_TEST"):
        logger.debug("opentelemetry_disabled")
        return

    endpoint = otlp_endpoint or os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://localhost:4318/v1/traces",
    )

    # Create resource identifying this service
    resource = Resource(
        attributes={
            "service.name": service_name,
            "service.version": os.environ.get("APP_VERSION", "0.1.0"),
            "deployment.environment": os.environ.get("ENV", "development"),
        }
    )

    # Set up tracing pipeline
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Auto-instrument supported libraries
    _instrument_libraries()

    _tracing_initialized = True
    logger.info("opentelemetry_initialized", service=service_name, endpoint=endpoint)


def _instrument_libraries() -> None:
    """Apply zero-code instrumentation to supported libraries.

    Each instrumentor is isolated in a try/except so a missing dependency
    won't crash the entire tracing setup.
    """
    # FastAPI — instrumented with the app instance in middleware_setup
    # (FastAPIInstrumentor.instrument_app), not here: it needs the app object.

    # SQLAlchemy
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
        logger.debug("instrumented_sqlalchemy")
    except Exception as exc:
        logger.warning("instrumentation_skipped", library="sqlalchemy", error=str(exc))

    # HTTPX (async HTTP client)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.debug("instrumented_httpx")
    except Exception as exc:
        logger.warning("instrumentation_skipped", library="httpx", error=str(exc))

    # Redis
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
        logger.debug("instrumented_redis")
    except Exception as exc:
        logger.warning("instrumentation_skipped", library="redis", error=str(exc))

    # Logging
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().instrument(set_logging_format=True)
        logger.debug("instrumented_logging")
    except Exception as exc:
        logger.warning("instrumentation_skipped", library="logging", error=str(exc))

    # aiohttp (used by some connectors)
    try:
        from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor

        AioHttpClientInstrumentor().instrument()
        logger.debug("instrumented_aiohttp")
    except Exception as exc:
        logger.warning("instrumentation_skipped", library="aiohttp", error=str(exc))


def get_tracer(name: str = "rmi-backend") -> trace.Tracer:
    """Get a named tracer for creating manual spans.

    Usage:
        tracer = get_tracer("scanner")
        with tracer.start_as_current_span("scan_token") as span:
            span.set_attribute("token", address)
            result = await perform_scan(address)
    """
    return trace.get_tracer(name)
