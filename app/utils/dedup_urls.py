"""Level 1: exact-URL dedup and the URL→story-group map.

The URL hash lives in a timestamp-scored ZSET (``rag:dedup:urls``) pruned
lazily; the URL hash → group_id map lives in a Redis hash. Both fail open:
a Redis outage means "not a duplicate" (ingestion proceeds).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.utils.dedup_config import URL_DEDUP_TTL_DAYS, URL_GROUP_HASH, URL_ZSET, get_redis, now_ts, prune_zset
from app.utils.dedup_hashing import url_hash

log = get_logger(__name__)


def is_duplicate_url(url: str) -> bool:
    """True when this exact URL was ingested within the 30-day window.

    Fail-open: Redis down/auth-failure → False (allow through).
    """
    if not url:
        return False
    try:
        r = get_redis()
        if r is None:
            log.debug("dedup_url_redis_unavailable url=%s allow=True", url)
            return False
        prune_zset(r, URL_ZSET, URL_DEDUP_TTL_DAYS)
        member = url_hash(url)
        score = r.zscore(URL_ZSET, member)
        if score is None:
            return False
        # Lazy per-member TTL: stale member → treat as new.
        if float(score) < now_ts() - URL_DEDUP_TTL_DAYS * 86400:
            r.zrem(URL_ZSET, member)
            return False
        return True
    except Exception as e:
        log.debug("dedup_url_check_failed url=%s err=%s allow=True", url, e)
        return False


def mark_url_ingested(url: str) -> None:
    """Record a URL as ingested (idempotent). Fail-open on Redis errors."""
    if not url:
        return
    try:
        r = get_redis()
        if r is None:
            return
        r.zadd(URL_ZSET, {url_hash(url): now_ts()})
    except Exception as e:
        log.debug("dedup_url_mark_failed url=%s err=%s", url, e)


def lookup_group_by_url(url: str) -> str | None:
    """Return the story group_id a URL was mapped to, if any."""
    try:
        r = get_redis()
        if r is None:
            return None
        val = r.hget(URL_GROUP_HASH, url_hash(url))
        return str(val) if val else None
    except Exception as e:
        log.debug("dedup_url_group_lookup_failed err=%s", e)
        return None
