"""
Circuit breaker and rate limiter for DataBus providers.

Tracks failures, rate limits, and quota usage across all provider instances.
"""

from __future__ import annotations

import time

from app.core.circuit_breaker import AsyncCircuitBreaker, SimpleCircuitBreaker
from app.core.logging import get_logger

logger = get_logger("databus.providers.circuit_breaker")

# Provider-specific circuit breakers (legacy, kept for backward compat)
_cb_coingecko = AsyncCircuitBreaker("coingecko", failure_threshold=5)
_cb_etherscan = AsyncCircuitBreaker("etherscan", failure_threshold=5)
_cb_moralis = AsyncCircuitBreaker("moralis", failure_threshold=5)
_cb_alchemy = AsyncCircuitBreaker("alchemy", failure_threshold=5)
_cb_default = AsyncCircuitBreaker("default", failure_threshold=3)


class _RateLimiter:
    """Simple rate limiter using minimum interval between calls."""

    def __init__(self, rps: float = 1.0) -> None:
        self.rps = rps
        self.min_interval = 1.0 / rps if rps > 0 else 0.0
        self.last_call: float = 0.0

    def can_call(self) -> bool:
        return time.time() - self.last_call >= self.min_interval

    def record_call(self) -> None:
        self.last_call = time.time()


# Global state (populated by build_provider_chains)
_circuit_breakers: dict[str, SimpleCircuitBreaker] = {}
_rate_limiters: dict[str, _RateLimiter] = {}
_quota_usage: dict[str, int] = {}
