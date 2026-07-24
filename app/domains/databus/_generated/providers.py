"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

Provider configuration definitions and helper factories.

This module re-exports the core Provider types from provider_core and
provides helper functions for building provider chain configurations.

Source: app/domains/databus/_generated/provider_chains.py (original god file).
"""

from typing import Any

from app.databus.provider_core import (
    Provider,
    ProviderChain,
    ProviderTier,
    _circuit_breakers,
    _CircuitBreaker,
    _rate_limiters,
    _RateLimiter,
)


def premium_provider(name: str, fn: Any, rps: float = 3.0) -> Provider:
    """Create a LOCAL-tier provider with weight=10.0 and given rate limit.

    Used by premium scanner chains and similar high-value detection chains.
    """
    return Provider(name, ProviderTier.LOCAL, fn, weight=10.0, rate_limit_rps=rps)


__all__ = [
    "Provider",
    "ProviderChain",
    "ProviderTier",
    "_CircuitBreaker",
    "_RateLimiter",
    "_circuit_breakers",
    "_rate_limiters",
    "premium_provider",
]
