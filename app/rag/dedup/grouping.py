"""Group management for deduplication."""
from __future__ import annotations

import contextlib
import hashlib
import time
from typing import Any

from app.rag.dedup._base import DedupResult

_GROUP_MEMBERS_PREFIX = "rag:dedup:group:members:"


def _get_redis() -> Any | None:
    from app.core.redis_client import get_redis
    try:
        return get_redis()
    except Exception:
        return None


def new_group_id(url: str) -> str:
    return f"grp:{hashlib.sha256((url or '').encode()).hexdigest()[:12]}"


def record_group_membership(group_id: str, url: str, collection: str, ttl_days: int = 30) -> None:
    r = _get_redis()
    if r is None:
        return
    key = f"{_GROUP_MEMBERS_PREFIX}{group_id}"
    with contextlib.suppress(Exception):
        r.sadd(key, url)
        r.expire(key, ttl_days * 86400)


def record_fp_group(fp: str, group_id: str, collection: str) -> None:
    r = _get_redis()
    if r is None:
        return
    key = f"rag:dedup:content:group:{collection}"
    with contextlib.suppress(Exception):
        r.hset(key, fp, group_id)


def group_from_hit(hit: Any) -> str | None:
    try:
        return hit.payload.get("group_id") if hasattr(hit, "payload") else None
    except Exception:
        return None
