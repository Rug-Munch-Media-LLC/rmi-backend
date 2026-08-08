"""
Request Deduplicator — deduplicate in-flight requests for the same data.

If two callers request token_price for the same token while a fetch
is already in progress, the second caller awaits the same future
instead of firing a duplicate API call.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any


class RequestDeduplicator:
    """Deduplicate in-flight requests for the same data type + kwargs.

    Usage:
        dedup = RequestDeduplicator()
        future, is_dup = await dedup.get_or_create(key)
        if not is_dup:
            result = await fetch_data()
            dedup.resolve(key, result)
        else:
            result = await future
    """

    def __init__(self) -> None:
        self._in_flight: dict[str, asyncio.Future] = {}
        self._dedup_hits = 0
        self._dedup_saved_calls = 0

    def make_key(self, data_type: str, **kwargs) -> str:
        """Build a deterministic dedup key from data_type + kwargs."""
        args_str = json.dumps(kwargs, sort_keys=True, default=str)
        args_hash = hashlib.sha256(args_str.encode()).hexdigest()[:16]
        return f"{data_type}:{args_hash}"

    async def get_or_create(self, key: str) -> tuple[asyncio.Future, bool]:
        """Get existing future or create a new one.

        Returns:
            Tuple of (future, is_duplicate). If is_duplicate is True,
            the caller should await the future instead of re-fetching.
        """
        if key in self._in_flight:
            self._dedup_hits += 1
            self._dedup_saved_calls += 1
            return self._in_flight[key], True
        future = asyncio.get_event_loop().create_future()
        self._in_flight[key] = future
        return future, False

    def resolve(self, key: str, result: Any) -> None:
        """Resolve an in-flight future and clean up."""
        future = self._in_flight.pop(key, None)
        if future and not future.done():
            future.set_result(result)

    def fail(self, key: str, exc: Exception) -> None:
        """Fail an in-flight future and clean up."""
        future = self._in_flight.pop(key, None)
        if future and not future.done():
            future.set_exception(exc)

    def stats(self) -> dict[str, Any]:
        """Return current dedup statistics."""
        return {
            "in_flight": len(self._in_flight),
            "dedup_hits": self._dedup_hits,
            "saved_api_calls": self._dedup_saved_calls,
        }
