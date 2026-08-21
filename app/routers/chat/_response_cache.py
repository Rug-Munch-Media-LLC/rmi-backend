"""In-process TTL response cache for the chat endpoint (speed for repeats).

Split out of ``app/routers/chat/__init__.py`` (de-godding phase 2,
behavior-preserving). ``_chat_cache`` is re-exported from the package root —
tests clear it via ``app.routers.chat._chat_cache`` and MUST see the same
dict object.
"""

from __future__ import annotations

import time

from ._rag import _CACHE_MAX_ENTRIES

# Small in-process TTL cache keyed on the normalized message. Repeated/common
# questions (e.g. "what is solana", "btc price") short-circuit the LLM
# round-trip entirely. Purely in-memory: no Redis, plain dict +
# ``time.monotonic``. NEVER caches the identity/vague canned answers (those
# fast-paths return before the cache is consulted) and NEVER caches LLM
# failures (only successful responses are stored).
_chat_cache: dict[str, tuple[float, str]] = {}


def _cache_get(key: str) -> str | None:
    """Return the cached response for ``key``, or None on miss/expiry."""
    entry = _chat_cache.get(key)
    if entry is None:
        return None
    expires_at, response = entry
    if time.monotonic() > expires_at:
        _chat_cache.pop(key, None)
        return None
    return response


def _cache_set(key: str, response: str, ttl: int) -> None:
    """Store ``response`` under ``key`` with a ``ttl`` (seconds).

    Evicts the oldest entries when the dict exceeds ``_CACHE_MAX_ENTRIES``.
    """
    _chat_cache[key] = (time.monotonic() + ttl, response)
    if len(_chat_cache) > _CACHE_MAX_ENTRIES:
        # Oldest = earliest expiry timestamp (monotonic, so insertion order).
        overflow = len(_chat_cache) - _CACHE_MAX_ENTRIES
        oldest_keys = sorted(_chat_cache, key=lambda k: _chat_cache[k][0])[:overflow]
        for k in oldest_keys:
            _chat_cache.pop(k, None)
