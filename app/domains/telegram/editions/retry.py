"""Retry policy: which failures are worth another attempt, and how long to wait.

Only transient failures are retried — flood-wait, timeout, connection, 5xx and
429. A permanent error (bad markup, chat not found, no rights) fails on the
first attempt, so a broken payload cannot burn the retry budget and delay every
other lane.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

_RETRYABLE_TOKENS = (
    "flood",
    "retryafter",
    "retry_after",
    "slowmode",
    "timeout",
    "timedout",
    "connect",
    "network",
    "servererror",
    "429",
    "500",
    "502",
    "503",
    "504",
)


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    """Flood-control and retry knobs. Defaults match Telegram's documented limits."""

    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    lane_min_interval_s: float = 3.0
    jitter: bool = True


def is_retryable(exc: BaseException) -> bool:
    """Transient Telegram/network failure? Unknown errors are treated as final."""
    probe = f"{type(exc).__name__} {exc}".lower()
    return any(token in probe for token in _RETRYABLE_TOKENS)


def backoff_delay(attempt: int, config: DeliveryConfig) -> float:
    """Exponential backoff, capped, optionally jittered (attempt is 1-based)."""
    raw = min(config.max_delay_s, config.base_delay_s * (2 ** (attempt - 1)))
    if not config.jitter:
        return raw
    return raw * random.uniform(0.5, 1.0)  # noqa: S311 — retry jitter, not crypto


__all__ = ["DeliveryConfig", "backoff_delay", "is_retryable"]
