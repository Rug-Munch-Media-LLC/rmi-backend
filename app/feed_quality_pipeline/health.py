"""
Provider health monitoring — circuit breakers, rolling stats, dashboards.

Exports:
    ProviderHealthMonitor
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from app.core.circuit_breaker import SimpleCircuitBreaker, State
from app.core.logging import get_logger
from app.feed_quality_pipeline.cache import CIRCUIT_BREAKER_RESET_SEC, CIRCUIT_BREAKER_THRESHOLD
from app.feed_quality_pipeline.models import ProviderHealth, ProviderStatus

logger = get_logger(__name__)


class ProviderHealthMonitor:
    """Tracks per-provider health with rolling windows."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderHealth] = {}
        self._circuit_breakers: dict[str, SimpleCircuitBreaker] = {}

    def register_provider(self, name: str) -> None:
        """Register a provider for health tracking."""
        if name not in self._providers:
            self._providers[name] = ProviderHealth(name=name)
            self._circuit_breakers[name] = SimpleCircuitBreaker(
                name=name,
                failure_threshold=CIRCUIT_BREAKER_THRESHOLD,
                recovery_timeout=CIRCUIT_BREAKER_RESET_SEC,
            )

    def record_fetch_start(self, name: str) -> float:
        """Record start of fetch, return start time."""
        if name not in self._providers:
            self.register_provider(name)
        self._providers[name].total_fetches += 1
        return time.monotonic()

    def record_fetch_success(self, name: str, start_time: float, post_count: int) -> None:
        """Record a successful fetch."""
        elapsed_ms = (time.monotonic() - start_time) * 1000
        h = self._providers.get(name)
        if not h:
            return

        h.successful_fetches += 1
        h.total_posts += post_count
        h.avg_response_time_ms = (h.avg_response_time_ms * (h.successful_fetches - 1) + elapsed_ms) / max(
            h.successful_fetches, 1
        )
        h.last_fetch = datetime.now(UTC).isoformat()
        h.last_error = ""
        h.circuit_open_until = 0.0

        # Track recent response times (rolling window)
        h.recent_response_times.append(elapsed_ms)
        if len(h.recent_response_times) > 100:
            h.recent_response_times = h.recent_response_times[-50:]

        if post_count == 0:
            h.empty_responses += 1

        # Update circuit breaker
        cb = self._circuit_breakers.get(name)
        if cb:
            cb.record_success()

        self._update_status(name)

    def record_fetch_failure(self, name: str, start_time: float, error: str) -> None:
        """Record a failed fetch."""
        h = self._providers.get(name)
        if not h:
            return

        h.failed_fetches += 1
        h.last_error = error[:200]
        h.last_fetch = datetime.now(UTC).isoformat()

        # Track recent errors
        h.recent_errors.append(error[:100])
        if len(h.recent_errors) > 20:
            h.recent_errors = h.recent_errors[-10:]

        # Update circuit breaker (error is already tracked in ProviderHealth.last_error)
        cb = self._circuit_breakers.get(name)
        if cb:
            cb.record_failure(is_server_error=True)

        self._update_status(name)

    def can_fetch(self, name: str) -> bool:
        """Check if we're allowed to fetch from this provider."""
        cb = self._circuit_breakers.get(name)
        if not cb:
            return True
        return cb.can_call()

    def get_provider_status(self, name: str) -> ProviderStatus:
        cb = self._circuit_breakers.get(name)
        if cb:
            if cb.state == State.OPEN:
                return ProviderStatus.CIRCUIT_OPEN
            elif cb.state == State.HALF_OPEN:
                return ProviderStatus.DEGRADED
            return ProviderStatus.HEALTHY
        return ProviderStatus.HEALTHY

    def _update_status(self, name: str) -> None:
        """Derive overall status from health + circuit breaker."""
        h = self._providers.get(name)
        if not h:
            return

        cb = self._circuit_breakers.get(name)
        if cb and cb.state == State.OPEN:
            h.status = ProviderStatus.CIRCUIT_OPEN
        elif h.failed_fetches > 0 and h.successful_fetches == 0:
            h.status = ProviderStatus.DOWN
        elif h.total_fetches > 0 and (h.failed_fetches / max(h.total_fetches, 1) > 0.5):
            h.status = ProviderStatus.DEGRADED
        else:
            h.status = ProviderStatus.HEALTHY

    def get_all_health(self) -> dict[str, ProviderHealth]:
        return dict(self._providers)

    def get_dashboard(self) -> dict[str, Any]:
        """Get provider health for dashboard display."""
        dashboard = {}
        for name, h in self._providers.items():
            cb = self._circuit_breakers.get(name)
            cb_state = cb.state.value if cb else "closed"
            dashboard[name] = {
                "status": h.status.value,
                "total_fetches": h.total_fetches,
                "successful": h.successful_fetches,
                "failed": h.failed_fetches,
                "empty_responses": h.empty_responses,
                "total_posts_ingested": h.total_posts,
                "avg_response_ms": round(h.avg_response_time_ms, 1),
                "last_fetch": h.last_fetch,
                "circuit_breaker": cb_state,
            }
        return dashboard


__all__ = ["ProviderHealthMonitor"]
