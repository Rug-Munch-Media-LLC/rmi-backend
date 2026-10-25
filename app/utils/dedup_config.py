"""Tuning knobs, Redis key names and the Redis DI seam for news dedup.

Every dedup level resolves its Redis client through one fail-open path:

  1. a client explicitly bound with :func:`bind_redis` (``ContextVar`` —
     per-task, no global mutation, no monkeypatching), else
  2. the app-wide sync client from ``app.core.redis`` (resolved lazily,
     retried on every call while it is unavailable so recovery is automatic),
     else
  3. ``None`` — callers fail open ("not a duplicate", ingestion proceeds).

This module imports nothing from the rest of the dedup package: it is the
bottom of the dependency graph.
"""

from __future__ import annotations

import time
from contextvars import ContextVar, Token
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

# ── Tuning knobs ─────────────────────────────────────────────────────────

URL_DEDUP_TTL_DAYS = 30  # per-URL-hash retention
CONTENT_DEDUP_TTL_DAYS = 30  # per-fingerprint retention
TITLE_SIM_THRESHOLD = 0.95  # Level 2: cosine similarity for "same title"
ENTITY_OVERLAP_THRESHOLD = 0.8  # Level 4: min overlap coefficient
ENTITY_WINDOW_HOURS = 6  # Level 4: max age gap between the two articles
GROUP_MEMBERS_TTL_DAYS = 30

# ── Redis key names ──────────────────────────────────────────────────────

URL_ZSET = "rag:dedup:urls"
CONTENT_ZSET_PREFIX = "rag:dedup:content:"
URL_GROUP_HASH = "rag:dedup:url:group"
CONTENT_GROUP_HASH_PREFIX = "rag:dedup:content:group:"
GROUP_MEMBERS_PREFIX = "rag:dedup:group:members:"

# ── Redis DI seam ────────────────────────────────────────────────────────

_redis_override: ContextVar[Any | None] = ContextVar("dedup_redis_client", default=None)
_redis_fallback: Any | None = None


def bind_redis(client: Any) -> Token:
    """Bind *client* as the dedup Redis client for the current context.

    Returns the ContextVar token; pass it to :func:`unbind_redis` (or let the
    context die with the task). ``None`` restores the app-wide fallback.
    """
    return _redis_override.set(client)


def unbind_redis(token: Token) -> None:
    """Undo a :func:`bind_redis` call."""
    _redis_override.reset(token)


def reset_redis_client() -> None:
    """Drop the cached fallback client (tests, or after a Redis reconnect)."""
    global _redis_fallback
    _redis_fallback = None


def get_redis() -> Any | None:
    """Resolve the sync Redis client; ``None`` when unavailable. Never raises."""
    override = _redis_override.get()
    if override is not None:
        return override
    global _redis_fallback
    if _redis_fallback is None:
        try:
            from app.core.redis import get_redis as _app_get_redis

            _redis_fallback = _app_get_redis()
        except Exception as e:  # pragma: no cover - defensive
            log.debug("dedup_redis_init_failed err=%s", e)
            _redis_fallback = None
    return _redis_fallback


# ── Time helpers ─────────────────────────────────────────────────────────


def now_ts() -> int:
    """Unix timestamp in whole seconds."""
    return int(time.time())


def prune_zset(r: Any, key: str, ttl_days: int) -> None:
    """Remove members older than *ttl_days* from a timestamp-scored ZSET."""
    try:
        r.zremrangebyscore(key, 0, now_ts() - ttl_days * 86400)
    except Exception as e:
        log.debug("dedup_prune_failed key=%s err=%s", key, e)
