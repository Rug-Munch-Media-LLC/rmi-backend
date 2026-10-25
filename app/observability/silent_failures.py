"""Visible degradation for handlers that previously failed silently.

Every swallowed failure now produces a WARNING with context and increments
`rmi_degraded_failures_total`, so an ingest/provider failure is measurable
instead of invisible.
"""

from __future__ import annotations

import logging

from prometheus_client import Counter

logger = logging.getLogger(__name__)

DEGRADED_FAILURES = Counter(
    "rmi_degraded_failures_total",
    "Failures previously swallowed silently, by subsystem and fallback",
    ["subsystem", "operation", "fallback", "failure"],
)


def record_silent_failure(
    subsystem: str,
    operation: str,
    exc: BaseException,
    *,
    fallback: str = "none",
) -> None:
    """Record a swallowed failure as a metric plus a WARNING log."""
    failure = type(exc).__name__
    DEGRADED_FAILURES.labels(subsystem, operation, fallback, failure).inc()
    logger.warning(
        "degraded_fallback subsystem=%s operation=%s fallback=%s failure=%s error=%s",
        subsystem,
        operation,
        fallback,
        failure,
        str(exc)[:200],
    )
