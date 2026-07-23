"""
Circuit breaker and rate limiter for DataBus providers.

Tracks failures, rate limits, and quota usage across all provider instances.
"""

import time
from typing import Any

from app.core.circuit_breaker import AsyncCircuitBreaker
from app.core.logging import get_logger

logger = get_logger("databus.providers.circuit_breaker")

# Provider-specific circuit breakers (legacy, kept for backward compat)
_cb_coingecko = AsyncCircuitBreaker("coingecko", failure_threshold=5)
_cb_etherscan = AsyncCircuitBreaker("etherscan", failure_threshold=5)
_cb_moralis = AsyncCircuitBreaker("moralis", failure_threshold=5)
_cb_alchemy = AsyncCircuitBreaker("alchemy", failure_threshold=5)
_cb_default = AsyncCircuitBreaker("default", failure_threshold=3)


class _CircuitBreaker:
    """Simple circuit breaker to prevent hammering failing providers."""

    def __init__(self, threshold: int = 5, timeout: float = 60.0) -> None:
        self.threshold = threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure: float = 0.0
        self.open = False

    def can_call(self) -> bool:
        if self.open:
            if time.time() - self.last_failure > self.timeout:
                self.open = False
                self.failures = 0
                return True
            return False
        return True

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.threshold:
            self.open = True

    def record_success(self) -> None:
        self.failures = 0
        self.open = False


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
_circuit_breakers: dict[str, _CircuitBreaker] = {}
_rate_limiters: dict[str, _RateLimiter] = {}
_quota_usage: dict[str, int] = {}
