"""Latency honesty for the deep health route.

A dependency that answers in five seconds is not healthy. This module owns the
single threshold policy shared by the store probes and the aggregator so that a
slow store can never be reported as plainly ``healthy``.

Thresholds are wall-clock per store, measured by the probe that ran the store's
own round trip -- never derived from the enclosing request's clock.
"""

from __future__ import annotations

from typing import Any

# A probe slower than this is reported as ``degraded``.
DEGRADED_MS = 2_000
# A probe slower than this is unhealthy, full stop.
UNHEALTHY_MS = 5_000

OK = "ok"
DEGRADED = "degraded"
UNHEALTHY = "unhealthy"


def classify_latency(result: dict[str, Any], latency_ms: int) -> dict[str, Any]:
    """Stamp a store's measured latency and downgrade it per the policy."""
    result["latency_ms"] = latency_ms
    if latency_ms >= UNHEALTHY_MS:
        result["latency_state"] = UNHEALTHY
        result["healthy"] = False
        if not result.get("error"):
            result["error"] = f"latency {latency_ms} ms >= {UNHEALTHY_MS} ms"
    elif latency_ms >= DEGRADED_MS:
        result["latency_state"] = DEGRADED
    else:
        result["latency_state"] = OK
    return result
