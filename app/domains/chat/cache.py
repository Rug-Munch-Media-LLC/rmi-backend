"""Fail-open Redis cache for the website chat (answers and retrieval hits).

Mirrors the legacy semantics of ``app/routers/chat/_response_cache.py``: only
successful, non-empty payloads are stored, a TTL is mandatory, and the key is a
sha256 digest so no user text or persona leaks into Redis. Every Redis fault
degrade to a miss — chat must never fail because the cache did.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

ANSWER_PREFIX = "chat:ans:v1:"
RETRIEVAL_PREFIX = "chat:ret:v1:"
ANSWER_TTL_S = 3600
ANSWER_TTL_MARKET_S = 60
RETRIEVAL_TTL_S = 300

_MARKET_KEYWORDS = ("price", "mcap", "chart", "now", "today")
_WHITESPACE_RE = re.compile(r"\s+")


def _digest(*parts: str) -> str:
    normalized = "\x00".join(_WHITESPACE_RE.sub(" ", p.strip().lower()) for p in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class ChatCache:
    """Async JSON+TTL facade over the shared Redis client."""

    ANSWER_TTL_S = ANSWER_TTL_S
    RETRIEVAL_TTL_S = RETRIEVAL_TTL_S

    @staticmethod
    def answer_key(question: str, persona: str, channel: str) -> str:
        return f"{ANSWER_PREFIX}{_digest(question, persona, channel)}"

    @staticmethod
    def retrieval_key(question: str, collections: str) -> str:
        return f"{RETRIEVAL_PREFIX}{_digest(question, collections)}"

    @staticmethod
    def answer_ttl_s(question: str) -> int:
        """Market/price answers go stale in a minute; everything else an hour."""
        low = question.lower()
        return ANSWER_TTL_MARKET_S if any(k in low for k in _MARKET_KEYWORDS) else ANSWER_TTL_S

    @staticmethod
    def _client() -> Any | None:
        try:
            from app.core.redis import get_redis_async

            return get_redis_async()
        except Exception as exc:
            log.debug("chat_cache_unavailable err=%s", type(exc).__name__)
            return None

    async def get_json(self, key: str) -> Any | None:
        client = self._client()
        if client is None:
            return None
        try:
            raw = await client.get(key)
        except Exception as exc:
            log.warning("chat_cache_get_failed err=%s", type(exc).__name__)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            log.warning("chat_cache_bad_payload prefix=%s", key[:13])
            return None

    async def set_json(self, key: str, value: Any, ttl_s: int) -> None:
        if not value:
            return  # never cache an empty/false payload (legacy semantics)
        client = self._client()
        if client is None:
            return
        try:
            await client.set(key, json.dumps(value), ex=ttl_s)
        except Exception as exc:
            log.warning("chat_cache_set_failed err=%s", type(exc).__name__)


chat_cache = ChatCache()
