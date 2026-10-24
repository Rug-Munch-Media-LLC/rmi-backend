"""Per-provider quota windows and circuit breakers (no I/O, clock-injected)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable

DAY_SECONDS: Final = 86_400.0
WINDOW_SECONDS: Final = 60.0


class QuotaWindow:
    """Request allowance per minute and per rolling day."""

    def __init__(self, rpm: int, daily_requests: int) -> None:
        self.rpm = rpm
        self.daily_requests = daily_requests
        self.minute_mark = 0.0
        self.minute_used = 0
        self.day_mark = 0.0
        self.day_used = 0

    def allow(self, now: float) -> bool:
        self._roll(now)
        if self.rpm and self.minute_used >= self.rpm:
            return False
        return not (self.daily_requests and self.day_used >= self.daily_requests)

    def record(self, now: float) -> None:
        self._roll(now)
        self.minute_used += 1
        self.day_used += 1

    def _roll(self, now: float) -> None:
        if now - self.minute_mark >= WINDOW_SECONDS:
            self.minute_mark, self.minute_used = now, 0
        if now - self.day_mark >= DAY_SECONDS:
            self.day_mark, self.day_used = now, 0


@dataclass(slots=True)
class Breaker:
    """Three-state breaker: closed → open → half-open."""

    fail_max: int = 2
    reset_after: float = 60.0
    failures: int = 0
    opened_at: float = 0.0

    def allow(self, now: float) -> bool:
        if not self.opened_at:
            return True
        return now - self.opened_at >= self.reset_after

    def success(self) -> None:
        self.failures, self.opened_at = 0, 0.0

    def failure(self, now: float) -> None:
        self.failures += 1
        if self.failures >= self.fail_max:
            self.opened_at = now

    @property
    def state(self) -> str:
        if not self.opened_at:
            return "closed"
        return "open"


@dataclass(slots=True)
class PoolState:
    """Health + quota bookkeeping for every provider in a pool."""

    spec: object
    clock: Callable[[], float] = time.monotonic
    breakers: dict[str, Breaker] = field(default_factory=dict)
    quotas: dict[str, QuotaWindow] = field(default_factory=dict)
    last_error: dict[str, str] = field(default_factory=dict)
    served: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for provider in self.spec.providers:  # type: ignore[attr-defined]
            self.breakers[provider.id] = Breaker()
            self.quotas[provider.id] = QuotaWindow(provider.rpm, provider.daily_requests)

    def candidates(self) -> list[object]:
        """Enabled providers that are healthy and inside quota, in order."""
        now = self.clock()
        return [
            provider
            for provider in self.spec.enabled()  # type: ignore[attr-defined]
            if self.breakers[provider.id].allow(now) and self.quotas[provider.id].allow(now)
        ]

    def record(self, provider_id: str, *, ok: bool, error: str = "") -> None:
        now = self.clock()
        self.quotas[provider_id].record(now)
        if ok:
            self.breakers[provider_id].success()
            self.served[provider_id] = self.served.get(provider_id, 0) + 1
            self.last_error.pop(provider_id, None)
            return
        self.breakers[provider_id].failure(now)
        if error:
            self.last_error[provider_id] = error[:200]

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Per-provider health for the ``/pool/health`` endpoint."""
        rows: dict[str, dict[str, object]] = {}
        for provider in self.spec.providers:  # type: ignore[attr-defined]
            quota = self.quotas[provider.id]
            rows[provider.id] = {
                "model": provider.model,
                "enabled": provider.enabled,
                "breaker": self.breakers[provider.id].state,
                "used_today": quota.day_used,
                "daily_requests": provider.daily_requests,
                "used_this_minute": quota.minute_used,
                "rpm": provider.rpm,
                "served": self.served.get(provider.id, 0),
                "last_error": self.last_error.get(provider.id),
            }
        return rows
