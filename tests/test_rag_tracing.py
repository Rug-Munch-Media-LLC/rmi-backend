"""Tests for app/rag/tracing.py — Phoenix (Arize) RAG observability.

Covers the fail-open contract: with Phoenix disabled (default) every
helper is a no-op and must never raise, even if the phoenix package or
the OTEL SDK are missing.
"""

from __future__ import annotations

import sys
import types

import pytest

from app.rag import tracing


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    """Reset module state before each test."""
    tracing._enabled = False
    tracing._setup_attempted = False
    yield
    tracing._enabled = False
    tracing._setup_attempted = False


def test_setup_phoenix_disabled_when_env_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIX_ENABLED", raising=False)
    assert tracing.setup_phoenix() is False
    assert tracing.is_phoenix_enabled() is False


def test_setup_phoenix_fail_open_when_package_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """PHOENIX_ENABLED=true but the phoenix package is missing → setup must
    return False without raising (fail-open: backend never depends on it)."""
    monkeypatch.setenv("PHOENIX_ENABLED", "true")

    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "phoenix.otel":
            raise ImportError("phoenix package not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert tracing.setup_phoenix() is False
    assert tracing.is_phoenix_enabled() is False


def test_setup_phoenix_register_lazy_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """register() configures the exporter lazily (no network at setup time);
    with the package present it enables tracing even if the server is not
    reachable yet. OTLP export failures are background/no-op, so span
    helpers still never raise."""
    monkeypatch.setenv("PHOENIX_ENABLED", "true")
    monkeypatch.setenv("PHOENIX_ENDPOINT", "http://127.0.0.1:1")  # nothing listens
    assert tracing.setup_phoenix() is True
    assert tracing.is_phoenix_enabled() is True
    # Span helpers still safe with a real (unreachable) exporter.
    with tracing.trace_span("rag.test.lazy") as span:
        span.set_attribute("k", "v")


def test_trace_span_noop_when_disabled() -> None:
    with tracing.trace_span("rag.test.disabled") as span:
        # Caller code must never crash on span helpers when Phoenix is off.
        span.set_attribute("k", "v")
        span.record_exception(ValueError("x"))
    assert tracing.is_phoenix_enabled() is False


def test_trace_span_with_attributes_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled path: span receives the declared attributes and records
    additional ones set by the caller (verified via a fake OTEL SDK)."""

    class FakeSpan:
        def __init__(self) -> None:
            self.attrs: dict[str, object] = {}

        def set_attribute(self, key: str, value: object) -> None:
            self.attrs[key] = value

    class FakeSpanCtx:
        def __init__(self, name: str) -> None:
            self.span = FakeSpan()

        def __enter__(self) -> FakeSpan:
            return self.span

        def __exit__(self, *args: object) -> None:
            return None

    class FakeTracer:
        def start_as_current_span(self, name: str) -> FakeSpanCtx:
            return FakeSpanCtx(name)

    class FakeTrace:
        @staticmethod
        def get_tracer(*args: object, **kwargs: object) -> FakeTracer:
            return FakeTracer()

    fake_mod = types.ModuleType("opentelemetry")
    fake_mod.trace = FakeTrace
    monkeypatch.setitem(sys.modules, "opentelemetry", fake_mod)

    tracing._enabled = True
    captured: list[dict[str, object]] = []

    with tracing.trace_span(
        "rag.test.enabled",
        {"openinference.span.kind": "EMBEDDING", "embedder.batch_size": 3},
    ) as span:
        span.set_attribute("embedder.result_count", 3)
        captured.append(span.attrs)  # type: ignore[attr-defined]

    assert captured, "span context manager did not yield a live span"
    attrs = captured[0]
    assert attrs["openinference.span.kind"] == "EMBEDDING"
    assert attrs["embedder.batch_size"] == 3
    assert attrs["embedder.result_count"] == 3
