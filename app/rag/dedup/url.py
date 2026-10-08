"""URL-level deduplication."""

from __future__ import annotations

import contextlib
import hashlib
import time
from typing import Any

_URL_ZSET = "rag:dedup:urls"
_URL_GROUP_HASH = "rag:dedup:url:group"


def _get_redis() -> Any | None:
    from app.core.redis_client import get_redis

    try:
        return get_redis()
    except Exception:
        return None


def url_hash(url: str) -> str:
    return hashlib.sha256((url or "").encode()).hexdigest()


def is_duplicate_url(url: str, ttl_days: int = 30) -> bool:
    r = _get_redis()
    if r is None:
        return False
    key = url_hash(url)
    try:
        return bool(r.zscore(_URL_ZSET, key))
    except Exception:
        return False


def mark_url_ingested(url: str, ttl_days: int = 30) -> None:
    r = _get_redis()
    if r is None:
        return
    key = url_hash(url)
    with contextlib.suppress(Exception):
        r.zadd(_URL_ZSET, {key: time.time()})
        r.expire(_URL_ZSET, ttl_days * 86400)


def lookup_group_by_url(url: str) -> str | None:
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.hget(_URL_GROUP_HASH, url_hash(url))
        return raw.decode() if raw else None
    except Exception:
        return None


def prune_zset(r: Any, key: str, ttl_days: int) -> None:
    """Remove members older than ttl_days from a sorted set."""
    cutoff = time.time() - ttl_days * 86400
    with contextlib.suppress(Exception):
        r.zremrangebyscore(key, "-inf", cutoff)
