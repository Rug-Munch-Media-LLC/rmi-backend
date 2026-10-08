"""Content-level deduplication."""

from __future__ import annotations

import contextlib
import time
from typing import Any

_CONTENT_ZSET_PREFIX = "rag:dedup:content:"
_CONTENT_GROUP_HASH_PREFIX = "rag:dedup:content:group:"


def _get_redis() -> Any | None:
    from app.core.redis_client import get_redis

    try:
        return get_redis()
    except Exception:
        return None


def is_duplicate_content(fp: str, collection: str, ttl_days: int = 30) -> bool:
    r = _get_redis()
    if r is None:
        return False
    key = f"{_CONTENT_ZSET_PREFIX}{collection}"
    try:
        return bool(r.zscore(key, fp))
    except Exception:
        return False


def mark_content_ingested(fp: str, collection: str, ttl_days: int = 30) -> None:
    r = _get_redis()
    if r is None:
        return
    key = f"{_CONTENT_ZSET_PREFIX}{collection}"
    with contextlib.suppress(Exception):
        r.zadd(key, {fp: time.time()})
        r.expire(key, ttl_days * 86400)


def lookup_group_by_fp(fp: str, collection: str) -> str | None:
    r = _get_redis()
    if r is None:
        return None
    key = f"{_CONTENT_GROUP_HASH_PREFIX}{collection}"
    with contextlib.suppress(Exception):
        raw = r.hget(key, fp)
        return raw.decode() if raw else None
    return None
