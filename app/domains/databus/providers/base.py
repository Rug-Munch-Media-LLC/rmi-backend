"""
Base provider classes and data types for DataBus.

Defines the core types: ProviderTier, Provider, and ProviderChain
with the smart fallback fetch logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("databus.providers")


class ProviderTier(Enum):
    """Cost tier classification for a data provider."""

    LOCAL = "local"  # Our own data — instant, free, unlimited
    FREE_API = "free_api"  # Free external API — no key needed
    FREEMIUM = "freemium"  # Free tier with key — limited credits
    PAID = "paid"  # Paid API — precious credits


@dataclass
class Provider:
    """A single data source in a fallback chain."""

    name: str
    tier: ProviderTier
    fetch_fn: Callable[..., Any] = field(repr=False)
    weight: float = 1.0  # Higher = preferred within tier
    rate_limit_rps: float = 1.0
    monthly_quota: int = 0  # 0 = unlimited
    requires_key: bool = False
    key_env: str = ""
    timeout: float = 15.0
    is_local: bool = False  # True if this uses our own data (no external API)
    description: str = ""  # Human-readable description
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    interval_minutes: int = 0  # Optional refresh cadence for scheduled providers (0 = no schedule)
    health_url: str = ""  # Lightweight endpoint used by health_check() (empty = no probe defined)

    async def health_check(self) -> dict[str, Any]:
        """Lightweight reachability probe for this provider.

        GETs ``health_url`` with a short timeout and measures latency.
        A provider is considered healthy when the server responds with any
        HTTP status < 500 (a 401/404 still proves the API is reachable —
        auth/route errors are not connectivity failures).

        Returns:
            {"healthy": bool | None, "latency_ms": int | None}
            healthy is None when no health_url is configured for this provider.
        """
        if not self.health_url:
            return {"healthy": None, "latency_ms": None}

        import time

        import httpx

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self.health_url)
            latency_ms = int((time.monotonic() - start) * 1000)
            healthy = resp.status_code < 500
            return {"healthy": healthy, "latency_ms": latency_ms}
        except (
            httpx.TimeoutException,
            ConnectionError,
            OSError,
            TimeoutError,
            httpx.HTTPError,
            ValueError,
            RuntimeError,
        ):
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"healthy": False, "latency_ms": latency_ms}


@dataclass
class ProviderChain:
    """A fallback chain for a specific data type.

    Tries each provider in priority order until one succeeds.
    Intelligently conserves paid credits when quotas are running low.
    """

    data_type: str
    providers: list[Provider]
    description: str = ""

    async def fetch(self, vault=None, cache=None, **kwargs) -> Any | None:
        """Try each provider in order until one succeeds.

        Smart fallback: when paid provider quota is >80% used, skip to free/local
        alternatives first to conserve credits for critical queries.
        """
        # Lazy import to avoid circular dependency at module level
        from app.core.circuit_breaker import SimpleCircuitBreaker

        from .circuit_breaker import (
            _circuit_breakers,
            _quota_usage,
            _rate_limiters,
            _RateLimiter,
        )

        providers_sorted = sorted(self.providers, key=lambda p: (-p.weight, p.tier.value))

        # ── Credit pressure: if paid providers are near quota, bump free providers up ──
        credit_pressure = False
        for p in providers_sorted:
            if p.monthly_quota > 0 and p.tier.value in ("paid", "freemium"):
                used = _quota_usage.get(p.name, 0)
                if used > p.monthly_quota * 0.8:  # 80% threshold
                    credit_pressure = True
                    logger.info(
                        f"Credit pressure: {p.name} at {used}/{p.monthly_quota} ({used * 100 // p.monthly_quota}%)"
                    )

        if credit_pressure:
            # Re-sort: push free/local providers above paid/freemium near quota
            providers_sorted.sort(key=lambda p: (0 if p.tier.value in ("local", "free_api") else 1, -p.weight))

        for provider in providers_sorted:
            # Check circuit breaker
            cb = _circuit_breakers.get(provider.name)
            if cb is not None and not cb.can_call():
                logger.debug(f"Circuit breaker open for {provider.name}")
                continue

            # Check rate limit
            if not _rate_limiters.get(provider.name, _RateLimiter()).can_call():
                logger.debug(f"Rate limit exceeded for {provider.name}")
                continue

            # Check quota
            if provider.monthly_quota > 0:
                used = _quota_usage.get(provider.name, 0)
                if used >= provider.monthly_quota:
                    logger.debug(f"Monthly quota exceeded for {provider.name}")
                    continue

            try:
                # Get API key from env
                api_key = None
                if provider.requires_key and provider.key_env:
                    import os

                    api_key = os.getenv(provider.key_env, "")

                result = await provider.fetch_fn(api_key=api_key, **kwargs)

                if result is not None:
                    _rate_limiters.setdefault(provider.name, _RateLimiter()).record_call()
                    if provider.monthly_quota > 0:
                        _quota_usage[provider.name] = _quota_usage.get(provider.name, 0) + 1
                    if provider.name not in _circuit_breakers:
                        _circuit_breakers[provider.name] = SimpleCircuitBreaker(
                            name=provider.name,
                            failure_threshold=provider.failure_threshold,
                            recovery_timeout=provider.recovery_timeout,
                        )
                    _circuit_breakers[provider.name].record_success()
                    return result

            except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError) as e:
                logger.warning(f"provider error: {e}")
                if provider.name not in _circuit_breakers:
                    _circuit_breakers[provider.name] = SimpleCircuitBreaker(
                        name=provider.name,
                        failure_threshold=provider.failure_threshold,
                        recovery_timeout=provider.recovery_timeout,
                    )
                _circuit_breakers[provider.name].record_failure(is_server_error=True)
                continue

        return None
