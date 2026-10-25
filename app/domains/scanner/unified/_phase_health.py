"""Resilience wiring for the scanner phase runners.

Split out of ``_phase_helpers.py`` (debt wave, 2026-10-25). Migrated to the
``rugmunch-scanner`` 0.2.0 API: one ``HealthTracker`` per phase (circuit
breaker) and ``timeout_decorator`` instead of the 0.1.x decorator-shaped
``with_timeout``.
"""

from __future__ import annotations

from typing import Any

# ── Resilience layer (rugmunch-scanner) ────────────────────────────────────
# Guarded import: the private ``rugmunch_scanner`` package may not be
# installed in dev environments. Fallbacks are no-ops so the scanner keeps
# working without the resilience layer (fail-open by contract).
try:
    from rugmunch_scanner.resilience import (
        TimeoutError as ScannerTimeoutError,
        get_tracker,
        timeout_decorator,
    )
except ImportError:  # pragma: no cover — dev env without the private package

    class ScannerTimeoutError(RuntimeError):  # type: ignore[no-redef]
        """No-op stand-in for ``rugmunch_scanner.resilience.TimeoutError``."""

        def __init__(self, operation: str = "", timeout_seconds: float = 0.0) -> None:
            self.timeout_seconds = timeout_seconds
            super().__init__(operation)

    def timeout_decorator(seconds: float, *, operation: str | None = None):  # type: ignore[misc]
        """No-op fallback for ``rugmunch_scanner.resilience.timeout_decorator``."""

        def decorator(func):
            return func

        return decorator

    class _NoopTracker:
        """No-op fallback for the ``rugmunch_scanner`` health tracker."""

        def can_proceed(self) -> bool:
            return True

        def record_success(self, latency_ms: float = 0.0) -> None:
            pass

        def record_failure(self, latency_ms: float | None = None) -> None:
            pass

    def get_tracker(name: str, *, failure_threshold: int = 5) -> Any:  # type: ignore[misc]
        return _NoopTracker()


#: Health tracker for the wallet_analysis phase: the circuit opens after 5
#: consecutive failures (skipping the phase) and half-opens 30s later, closing
#: again after 3 consecutive successes.
health_tracker = get_tracker("wallet_analysis", failure_threshold=5)
