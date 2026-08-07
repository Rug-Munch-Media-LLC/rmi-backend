"""
Manual span helpers for business-logic visibility.

Provides a decorator and context manager for adding manual OpenTelemetry spans
beyond the auto-instrumented framework layers. Use these to instrument
high-value business operations such as token scanning, wallet analysis,
or external API calls.

Usage:
    from app.observability.spans import traced, span

    # Decorator — wraps an entire function
    @traced("validate_token_address")
    async def validate_token(address: str) -> dict:
        ...

    # Context manager — wraps a specific code block
    with span("enrich_token_metadata", address=addr):
        metadata = await fetch_metadata(addr)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from functools import wraps
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

from opentelemetry import trace

_tracer = trace.get_tracer("rmi-backend.business")


@contextmanager
def span(name: str, **attributes: Any) -> Generator[trace.Span, None, None]:
    """Context manager that creates a named span with optional attributes.

    Example:
        with span("fetch_token_price", token=addr, chain="solana"):
            price = await get_price(addr)
    """
    with _tracer.start_as_current_span(name) as active_span:
        for key, value in attributes.items():
            active_span.set_attribute(key, value)
        yield active_span


@asynccontextmanager
async def async_span(name: str, **attributes: Any) -> AsyncGenerator[trace.Span, None]:
    """Async context manager version of ``span`` for use in async contexts.

    Example:
        async with async_span("scan_token", address=addr):
            result = await scanner.scan(addr)
    """
    with _tracer.start_as_current_span(name) as active_span:
        for key, value in attributes.items():
            active_span.set_attribute(key, value)
        yield active_span


def traced(name: str) -> Any:
    """Decorator that wraps an async or sync function in a named span.

    The function's ``__qualname__`` is set as a ``function`` attribute on
    the span for traceability.

    Example:
        @traced("validate_token")
        async def validate(address: str) -> dict:
            ...

        @traced("sync_operation")
        def compute_score(data: dict) -> float:
            ...
    """

    def decorator(func: Any) -> Any:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with _tracer.start_as_current_span(name) as active_span:
                active_span.set_attribute("function", func.__qualname__)
                return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with _tracer.start_as_current_span(name) as active_span:
                active_span.set_attribute("function", func.__qualname__)
                return func(*args, **kwargs)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
