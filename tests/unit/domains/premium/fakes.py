"""In-memory stand-ins for the two stores the watch cycle touches.

Not mocks: they implement the exact operations the production code calls
(``hset``/``hkeys``/``hlen``, ``execute`` → ``rowcount``), so a test that
passes here passes against the real client's contract. Anything the code stops
calling disappears from the fake and the test fails loudly.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any


class FakeRedis:
    """Async Redis subset used by ``watch_store``."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.kv: dict[str, str] = {}

    async def hset(self, key: str, field: str, value: str) -> int:
        bucket = self.hashes.setdefault(key, {})
        new = field not in bucket
        bucket[field] = value
        return 1 if new else 0

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def hkeys(self, key: str) -> list[str]:
        return list(self.hashes.get(key, {}))

    async def hdel(self, key: str, field: str) -> int:
        return 1 if self.hashes.get(key, {}).pop(field, None) is not None else 0

    async def sadd(self, key: str, member: str) -> int:
        bucket = self.sets.setdefault(key, set())
        new = member not in bucket
        bucket.add(member)
        return 1 if new else 0

    async def srem(self, key: str, member: str) -> int:
        bucket = self.sets.get(key, set())
        present = member in bucket
        bucket.discard(member)
        return 1 if present else 0

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def scan_iter(self, match: str = "*", **_kwargs: Any) -> Any:
        for key in list(set(self.hashes) | set(self.sets) | set(self.kv)):
            if fnmatch(key, match):
                yield key

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.kv[key] = value
        return True

    async def expire(self, key: str, ttl: int) -> bool:
        return key in self.hashes or key in self.sets or key in self.kv


class FakeResult:
    """The only part of a SQLAlchemy result ``save_alerts`` reads."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class FakeSession:
    """Minimal session: primary-key conflict behaves like ``ON CONFLICT``."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.commits = 0

    async def execute(self, _statement: Any, params: dict[str, Any]) -> FakeResult:
        alert_id = params["alert_id"]
        if alert_id in self.rows:
            return FakeResult(0)
        self.rows[alert_id] = params
        return FakeResult(1)

    async def commit(self) -> None:
        self.commits += 1
