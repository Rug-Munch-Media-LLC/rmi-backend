"""
Provider Health Tracker — circuit-breaker-aware per-provider health tracking.

Tracks success/failure rates, latency EMA, and circuit breaker state.
After 3 consecutive failures a provider's circuit is opened for 30 seconds.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.provider_health")


class ProviderHealthTracker:
    """Track per-provider health: success/failure rates, latency, availability."""

    def __init__(self) -> None:
        self._providers: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "total_calls": 0,
                "successes": 0,
                "failures": 0,
                "consecutive_failures": 0,
                "last_success": None,
                "last_failure": None,
                "avg_latency_ms": 0,
                "is_available": True,
                "circuit_open_until": 0,
            }
        )

    def record_success(self, provider_name: str, latency_ms: float) -> None:
        """Record a successful provider call."""
        p = self._providers[provider_name]
        p["total_calls"] += 1
        p["successes"] += 1
        p["consecutive_failures"] = 0
        p["last_success"] = time.time()
        p["is_available"] = True
        if p["avg_latency_ms"] == 0:
            p["avg_latency_ms"] = latency_ms
        else:
            p["avg_latency_ms"] = p["avg_latency_ms"] * 0.8 + latency_ms * 0.2

    def record_failure(self, provider_name: str, error: str = "") -> None:
        """Record a failed provider call (may open circuit breaker)."""
        p = self._providers[provider_name]
        p["total_calls"] += 1
        p["failures"] += 1
        p["consecutive_failures"] += 1
        p["last_failure"] = time.time()
        p["last_error"] = error
        if p["consecutive_failures"] >= 3:
            p["circuit_open_until"] = time.monotonic() + 30
            p["is_available"] = False
            logger.warning(f"Provider '{provider_name}' circuit breaker OPEN (3 consecutive failures)")

    def is_available(self, provider_name: str) -> bool:
        """Check if a provider is available (respecting circuit breaker)."""
        p = self._providers[provider_name]
        if not p["is_available"]:
            if time.monotonic() > p["circuit_open_until"]:
                p["is_available"] = True
                logger.info(f"Provider '{provider_name}' circuit breaker HALF-OPEN (probing)")
                return True
            return False
        return True

    def get_health(self, provider_name: str) -> dict[str, Any]:
        """Get health data for a single provider."""
        return dict(self._providers.get(provider_name, {}))

    def all_health(self) -> dict[str, dict[str, Any]]:
        """Return health data for all tracked providers with computed metrics."""
        result: dict[str, Any] = {}
        for name, data in self._providers.items():
            total = data["total_calls"]
            result[name] = {
                **data,
                "success_rate": round(data["successes"] / total * 100, 1) if total > 0 else 0,
                "circuit_open_until": data["circuit_open_until"] - time.monotonic()
                if data["circuit_open_until"] > time.monotonic()
                else 0,
            }
        return result
