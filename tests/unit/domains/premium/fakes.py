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

    def __init__(self, decode: bool = False) -> None:
        # The app pool is built without decode_responses, so reads come back as
        # bytes; keep that fidelity here or a str-only bug cannot be caught.
        self.decode = decode
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    def _out(self, value: str) -> str | bytes:
        return value if self.decode else value.encode()

    def _in(self, value: str | bytes) -> str:
        return value.decode() if isinstance(value, bytes) else value

    async def hset(self, key: str, field: str, value: str) -> int:
        bucket = self.hashes.setdefault(key, {})
        new = field not in bucket
        bucket[field] = value
        return 1 if new else 0

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def hkeys(self, key: str) -> list[str]:
        return [self._out(k) for k in self.hashes.get(key, {})]  # type: ignore[list-item]

    async def hdel(self, key: str, field: str) -> int:
        return 1 if self.hashes.get(key, {}).pop(field, None) is not None else 0

    async def lpush(self, key: str, value: str) -> int:
        bucket = self.lists.setdefault(key, [])
        bucket.insert(0, self._in(value))
        return len(bucket)

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
        return {self._out(m) for m in self.sets.get(key, set())}  # type: ignore[misc]

    async def scan_iter(self, match: str = "*", **_kwargs: Any) -> Any:
        for key in list(set(self.hashes) | set(self.sets) | set(self.kv)):
            if fnmatch(key, match):
                yield self._out(key)

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
