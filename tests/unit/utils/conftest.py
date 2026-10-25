"""Fake Redis + DI fixtures for the dedup unit tests.

The dedup package resolves Redis through ``bind_redis()`` (a ContextVar), so
tests inject a client instead of patching module internals.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.utils.dedup import bind_redis, unbind_redis


class FakeRedis:
    """Minimal sync-Redis stand-in covering the commands dedup uses."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.zsets: dict[str, dict[str, float]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.expires: dict[str, int] = {}

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis unavailable")

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self._check()
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zscore(self, key: str, member: str) -> float | None:
        self._check()
        return self.zsets.get(key, {}).get(member)

    def zremrangebyscore(self, key: str, minimum: float, maximum: float) -> int:
        self._check()
        zset = self.zsets.get(key, {})
        stale = [m for m, score in zset.items() if minimum <= score <= maximum]
        for member in stale:
            del zset[member]
        return len(stale)

    def zrem(self, key: str, member: str) -> int:
        self._check()
        return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0

    def hset(self, key: str, field: str, value: Any) -> int:
        self._check()
        self.hashes.setdefault(key, {})[field] = str(value)
        return 1

    def hget(self, key: str, field: str) -> str | None:
        self._check()
        return self.hashes.get(key, {}).get(field)

    def sadd(self, key: str, *members: str) -> int:
        self._check()
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    def expire(self, key: str, ttl: int) -> bool:
        self._check()
        self.expires[key] = ttl
        return True

    def pipeline(self) -> FakePipeline:
        self._check()
        return FakePipeline(self)


class FakePipeline:
    """Buffers commands and applies them on ``execute()`` like a real pipeline."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    def hset(self, *args: Any) -> FakePipeline:
        self._ops.append(("hset", args))
        return self

    def sadd(self, *args: Any) -> FakePipeline:
        self._ops.append(("sadd", args))
        return self

    def expire(self, *args: Any) -> FakePipeline:
        self._ops.append(("expire", args))
        return self

    def execute(self) -> list[Any]:
        return [getattr(self._redis, name)(*args) for name, args in self._ops]


@pytest.fixture
def fake_redis() -> Any:
    """Bind a working fake Redis client for the duration of the test."""
    client = FakeRedis()
    token = bind_redis(client)
    try:
        yield client
    finally:
        unbind_redis(token)


@pytest.fixture
def dead_redis() -> Any:
    """Bind a Redis client whose every command raises (outage simulation)."""
    client = FakeRedis(fail=True)
    token = bind_redis(client)
    try:
        yield client
    finally:
        unbind_redis(token)
