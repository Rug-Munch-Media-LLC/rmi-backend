"""
Base provider classes and data types for DataBus.

Defines the core types: ProviderTier, Provider, and ProviderChain
with the smart fallback fetch logic.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.logging import get_logger

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
    fetch_fn: Callable = field(repr=False)
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
        from .circuit_breaker import (
            _circuit_breakers,
            _CircuitBreaker,
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
                        f"Credit pressure: {p.name} at {used}/{p.monthly_quota} "
                        f"({used * 100 // p.monthly_quota}%)"
                    )

        if credit_pressure:
            # Re-sort: push free/local providers above paid/freemium near quota
            providers_sorted.sort(
                key=lambda p: (0 if p.tier.value in ("local", "free_api") else 1, -p.weight)
            )

        for provider in providers_sorted:
            # Check circuit breaker
            if not _circuit_breakers.get(provider.name, _CircuitBreaker()).can_call():
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
                    _circuit_breakers.setdefault(provider.name, _CircuitBreaker()).record_success()
                    return result

            except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
                logger.warning(f"provider error: {e}")
                _circuit_breakers.setdefault(provider.name, _CircuitBreaker()).record_failure()
                continue

        return None
