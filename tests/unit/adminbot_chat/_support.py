"""Fakes shared by the adminbot notification tests.

No monkeypatching: the clock, the severity rating and the alert outbox are
constructor parameters, so the real publisher runs against these.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.adminbot.notify.incident import Incident


class MemoryStore:
    """The HealthStore surface the publisher uses, backed by one record."""

    def __init__(self, start: float = 1_000_000.0, step: float = 300.0) -> None:
        self.now_value = start
        self.step = step
        self.state = Incident(start, 0.0, 0.0, start)

    async def claim(self) -> bool:
        return True

    async def now(self) -> float:
        """Advance the fake clock one watch tick, so debounce is reachable."""
        self.now_value += self.step
        return self.now_value

    async def load(self, now: float) -> Incident:
        return self.state

    async def save(self, incident: Incident, status: str) -> None:
        self.state = incident


class FakeRedis:
    """The minimum async surface HealthStore uses, shared by all workers."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.keys: set[str] = set()

    async def set(self, key: str, value: str, nx: bool = False, ex: int = 0) -> bool:
        if nx and key in self.keys:
            return False
        self.keys.add(key)
        return True

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.hashes.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def expire(self, key: str, ttl: int) -> bool:
        return key in self.keys or key in self.hashes


class BrokenRedis:
    """A Redis that always fails, proving the plane degrades open."""

    async def set(self, *_args: Any, **_kwargs: Any) -> bool:
        raise ConnectionError("redis down")

    async def hgetall(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        raise ConnectionError("redis down")


def ladder_rating(*severities: str):
    """Return a rating seam that walks a fixed ladder, then holds the last rung."""
    planned = list(severities)

    def rate(_sample: dict[str, float]) -> tuple[str, str]:
        level = planned.pop(0) if len(planned) > 1 else planned[0]
        return level, "load15 26.4 (>= 24.0) on 16 cores"

    return rate
