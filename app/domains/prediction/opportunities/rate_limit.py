"""Per-host request budget + 429 backoff for Polymarket upstreams.

Polymarket publishes no quota for Gamma / Data API, so the budget is
conservative and self-imposed: we never fire more than N requests per host per
minute and we honour ``Retry-After`` on 429. The tracker is reported back in the
API response so a consumer can see we degraded instead of guessing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

WINDOW_SECONDS = 60.0
DEFAULT_LIMIT_PER_MINUTE = {
    "gamma-api.polymarket.com": 60,
    "data-api.polymarket.com": 30,
}
DEFAULT_THROTTLE_SECONDS = 10.0


@dataclass
class HostBudget:
    """Sliding-window budget for one upstream host."""

    host: str
    limit_per_minute: int
    hits: list[float] = field(default_factory=list)
    allowed: int = 0
    refused: int = 0
    throttled: int = 0
    blocked_until: float = 0.0

    def allow(self) -> bool:
        now = time.monotonic()
        self.hits = [t for t in self.hits if now - t < WINDOW_SECONDS]
        if now < self.blocked_until:
            self.refused += 1
            return False
        if len(self.hits) >= self.limit_per_minute:
            self.refused += 1
            return False
        self.hits.append(now)
        self.allowed += 1
        return True

    def penalise(self, seconds: float = DEFAULT_THROTTLE_SECONDS) -> None:
        """Upstream returned 429 / 5xx: stop hitting this host for a while."""
        self.throttled += 1
        self.blocked_until = time.monotonic() + seconds

    def as_dict(self) -> dict[str, float | int | bool]:
        return {
            "host": self.host,
            "limit_per_minute": self.limit_per_minute,
            "allowed": self.allowed,
            "refused": self.refused,
            "throttled": self.throttled,
            "cooling_down": time.monotonic() < self.blocked_until,
        }


class RateLimitTracker:
    """Lazily creates one :class:`HostBudget` per host, never raises."""

    def __init__(self, limits: dict[str, int] | None = None) -> None:
        self._limits = dict(DEFAULT_LIMIT_PER_MINUTE if limits is None else limits)
        self._hosts: dict[str, HostBudget] = {}

    def budget(self, host: str) -> HostBudget:
        if host not in self._hosts:
            self._hosts[host] = HostBudget(
                host=host,
                limit_per_minute=self._limits.get(host, 30),
            )
        return self._hosts[host]

    def allow(self, host: str) -> bool:
        return self.budget(host).allow()

    def penalise(self, host: str, seconds: float = DEFAULT_THROTTLE_SECONDS) -> None:
        self.budget(host).penalise(seconds)

    def summary(self) -> dict[str, float | int | bool]:
        hosts = [b.as_dict() for b in self._hosts.values()]
        return {
            "hosts": hosts,
            "total_allowed": sum(int(b["allowed"]) for b in hosts),
            "total_refused": sum(int(b["refused"]) for b in hosts),
            "total_throttled": sum(int(b["throttled"]) for b in hosts),
        }
