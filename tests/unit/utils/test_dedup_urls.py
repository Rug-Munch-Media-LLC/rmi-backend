"""Level 1: exact-URL dedup, TTL pruning and the URL→group map."""

from __future__ import annotations

import time
from typing import Any

from app.utils.dedup import (
    is_duplicate_url,
    lookup_group_by_url,
    mark_url_ingested,
    new_group_id,
    record_group_membership,
    url_hash,
)
from app.utils.dedup_config import URL_DEDUP_TTL_DAYS, URL_ZSET

URL = "https://example.com/btc-etf-approved"


def test_unknown_url_is_not_duplicate(fake_redis: Any) -> None:
    assert is_duplicate_url(URL) is False


def test_mark_then_check_is_duplicate(fake_redis: Any) -> None:
    mark_url_ingested(URL)
    assert fake_redis.zscore(URL_ZSET, url_hash(URL)) is not None
    assert is_duplicate_url(URL) is True


def test_other_url_stays_new(fake_redis: Any) -> None:
    mark_url_ingested(URL)
    assert is_duplicate_url("https://example.com/other") is False


def test_stale_member_is_pruned_and_allowed(fake_redis: Any) -> None:
    """A member older than the TTL is removed lazily and treated as new."""
    stale = int(time.time()) - (URL_DEDUP_TTL_DAYS + 1) * 86400
    fake_redis.zadd(URL_ZSET, {url_hash(URL): stale})
    assert is_duplicate_url(URL) is False
    assert fake_redis.zscore(URL_ZSET, url_hash(URL)) is None


def test_mark_is_idempotent(fake_redis: Any) -> None:
    mark_url_ingested(URL)
    first = fake_redis.zscore(URL_ZSET, url_hash(URL))
    mark_url_ingested(URL)
    assert fake_redis.zscore(URL_ZSET, url_hash(URL)) is not None
    assert len(fake_redis.zsets[URL_ZSET]) == 1
    assert first is not None


def test_empty_url_never_marks(fake_redis: Any) -> None:
    assert is_duplicate_url("") is False
    mark_url_ingested("")
    assert URL_ZSET not in fake_redis.zsets


def test_group_membership_roundtrip(fake_redis: Any) -> None:
    group = new_group_id(URL)
    record_group_membership(group, URL, "news_v2")
    assert lookup_group_by_url(URL) == group
    assert URL in fake_redis.sets[f"rag:dedup:group:members:{group}"]
    assert fake_redis.expires[f"rag:dedup:group:members:{group}"] > 0


def test_group_lookup_unknown_url_is_none(fake_redis: Any) -> None:
    assert lookup_group_by_url("https://example.com/never-seen") is None


def test_redis_outage_fails_open(dead_redis: Any) -> None:
    """Redis down/auth-failure must allow ingestion, never raise."""
    assert is_duplicate_url(URL) is False
    mark_url_ingested(URL)
    assert lookup_group_by_url(URL) is None
