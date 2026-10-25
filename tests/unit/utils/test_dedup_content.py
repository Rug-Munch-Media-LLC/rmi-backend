"""Level 3: boilerplate stripping, fingerprinting and content-hash dedup."""

from __future__ import annotations

import hashlib
from typing import Any

from app.utils.dedup import (
    content_fingerprint,
    content_hash,
    is_duplicate_content,
    lookup_group_by_fp,
    mark_content_ingested,
    near_duplicate,
    normalize_text,
    record_fp_group,
    strip_boilerplate,
    tokenize,
)
from app.utils.dedup_config import CONTENT_GROUP_HASH_PREFIX, CONTENT_ZSET_PREFIX

BODY = "Ethereum ETF approved by the SEC after a long review.\nThe market reacted strongly."


def test_fingerprint_ignores_case_and_whitespace(fake_redis: Any) -> None:
    variant = "  ethereum   ETF approved\n\nby the SEC after a long review.   The market reacted strongly.  "
    assert content_fingerprint(BODY) == content_fingerprint(variant)


def test_fingerprint_strips_boilerplate(fake_redis: Any) -> None:
    noisy = f"Subscribe to our newsletter\n{BODY}\nAll rights reserved\nRead more\n"
    assert content_fingerprint(noisy) == content_fingerprint(BODY)


def test_fingerprint_truncates_at_1000_chars(fake_redis: Any) -> None:
    head = "a " * 600  # 1200 chars of body, truncated to 1000
    assert content_fingerprint(head + "tail-one") == content_fingerprint(head + "tail-two")


def test_fingerprint_empty_is_sha256_of_empty(fake_redis: Any) -> None:
    assert content_fingerprint("") == hashlib.sha256(b"").hexdigest()
    assert content_fingerprint("") == content_fingerprint("   \n\n  ")


def test_strip_boilerplate_keeps_body_lines() -> None:
    text = "Home\nReal line one\nhttps://ad.example/x\nReal line two"
    assert strip_boilerplate(text) == "Real line one Real line two"


def test_mark_then_check_is_duplicate(fake_redis: Any) -> None:
    fp = content_fingerprint(BODY)
    assert is_duplicate_content(fp, "news_v2") is False
    mark_content_ingested(fp, "news_v2")
    assert is_duplicate_content(fp, "news_v2") is True
    assert fake_redis.zscore(f"{CONTENT_ZSET_PREFIX}news_v2", fp) is not None


def test_collections_are_isolated(fake_redis: Any) -> None:
    fp = content_fingerprint(BODY)
    mark_content_ingested(fp, "news_v2")
    assert is_duplicate_content(fp, "other") is False


def test_empty_fingerprint_is_never_duplicate(fake_redis: Any) -> None:
    assert is_duplicate_content("", "news_v2") is False
    mark_content_ingested("", "news_v2")
    assert CONTENT_ZSET_PREFIX + "news_v2" not in fake_redis.zsets


def test_fp_group_roundtrip(fake_redis: Any) -> None:
    fp = content_fingerprint(BODY)
    record_fp_group(fp, "grp:abc123", "news_v2")
    assert lookup_group_by_fp(fp, "news_v2") == "grp:abc123"
    assert fake_redis.hget(f"{CONTENT_GROUP_HASH_PREFIX}news_v2", fp) == "grp:abc123"
    assert lookup_group_by_fp(fp, "other") is None


def test_redis_outage_fails_open(dead_redis: Any) -> None:
    fp = content_fingerprint(BODY)
    assert is_duplicate_content(fp, "news_v2") is False
    mark_content_ingested(fp, "news_v2")
    assert lookup_group_by_fp(fp, "news_v2") is None


def test_content_hash_keeps_full_body() -> None:
    long_text = "word " * 400
    assert content_hash(long_text + "tail") != content_hash(long_text + "other")
    assert len(content_hash(BODY)) == 64


def test_normalize_text_and_tokenize() -> None:
    assert normalize_text("  Foo\n\nBAR  ") == "foo bar"
    assert tokenize("Foo, bar_baz 42!") == {"foo", "bar", "baz", "42"}
    assert normalize_text("") == ""
    assert tokenize("") == set()


def test_near_duplicate_boundaries() -> None:
    assert near_duplicate("alpha beta gamma", "alpha beta gamma") is True
    assert near_duplicate("alpha beta gamma", "one two three") is False
    assert near_duplicate("", "") is False
    assert near_duplicate("alpha beta", "") is False
    assert near_duplicate("alpha beta gamma delta", "alpha beta", threshold=0.4) is True
