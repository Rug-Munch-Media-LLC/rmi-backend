"""Phoenix (Arize) RAG observability — fail-open OTEL tracing.

Contract (see tests/test_rag_tracing.py):
- ``setup_phoenix()`` returns ``bool`` — True only when PHOENIX_ENABLED is
  set AND the phoenix/OTEL packages import. Never raises (fail-open).
- ``is_phoenix_enabled()`` reports the current state.
- ``trace_span(...)`` yields a live OTEL span when enabled, else a no-op
  span that still accepts ``set_attribute`` / ``record_exception`` so
  caller code never crashes when Phoenix is off.
"""

from __future__ import annotations

import contextlib
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

_enabled = False
_setup_attempted = False
_tracer: Any = None


def is_phoenix_enabled() -> bool:
    """True when Phoenix tracing was successfully set up."""
    return _enabled


def setup_phoenix() -> bool:
    """Initialize Phoenix tracing if enabled via env. Never raises.

    Returns True when the tracer provider was registered; False when
    disabled by env or when the phoenix/OTEL packages are unavailable
    (fail-open — the backend never depends on tracing being up).
    """
    global _enabled, _setup_attempted, _tracer

    if os.getenv("PHOENIX_ENABLED", "").strip().lower() not in ("1", "true", "yes", "on"):
        _enabled = False
        _setup_attempted = True
        return False

    if _setup_attempted:
        return _enabled
    _setup_attempted = True

    try:
        import phoenix.otel

        endpoint = os.getenv("PHOENIX_ENDPOINT", "http://localhost:6006")
        # register() configures the OTLP exporter lazily — no network at
        # setup time; export failures are background/no-op.
        _tracer = phoenix.otel.register(endpoint=endpoint)
        _enabled = True
        return True
    except ImportError:
        # phoenix extra not installed — fail-open per contract.
        _enabled = False
        return False
    except Exception:
        _enabled = False
        return False


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Generator[Any, None, None]:
    """Span context manager — live OTEL span when enabled, no-op otherwise."""

    class _NoopSpan:
        def set_attribute(self, *a: object, **k: object) -> None:
            return None

        def set_attributes(self, *a: object, **k: object) -> None:
            return None

        def record_exception(self, *a: object, **k: object) -> None:
            return None

    if not _enabled:
        yield _NoopSpan()
        return

    tracer = _tracer
    if tracer is None or not hasattr(tracer, "start_as_current_span"):
        try:
            import opentelemetry as _otel_pkg

            tracer = _otel_pkg.trace.get_tracer(__name__)
        except Exception:
            yield _NoopSpan()
            return

    span_ctx = tracer.start_as_current_span(name)
    span = span_ctx.__enter__()
    for key, value in (attributes or {}).items():
        with contextlib.suppress(Exception):
            span.set_attribute(key, value)
    try:
        yield span
    finally:
        span_ctx.__exit__(None, None, None)
