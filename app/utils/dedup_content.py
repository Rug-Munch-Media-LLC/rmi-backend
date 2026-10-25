"""Level 3: boilerplate stripping and content-fingerprint dedup.

``content_fingerprint`` survives formatting churn (nav/footer/ads/CTAs) while
staying sensitive to real body changes; ``is_duplicate_content`` /
``mark_content_ingested`` keep the fingerprints in a per-collection Redis
ZSET. Every Redis call fails open.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.core.logging import get_logger
from app.utils.dedup_config import (
    CONTENT_DEDUP_TTL_DAYS,
    CONTENT_GROUP_HASH_PREFIX,
    CONTENT_ZSET_PREFIX,
    get_redis,
    now_ts,
    prune_zset,
)

log = get_logger(__name__)

# Boilerplate lines to strip before fingerprinting (footer/nav/ads/CTAs).
_BOILERPLATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?i)^\s*(subscribe|follow us|follow me|sign up|newsletter|join our)",
    ),
    re.compile(r"(?i)^\s*(cookie|cookies|privacy policy|terms of service|terms of use)"),
    re.compile(r"(?i)^\s*(advertisement|advertising|sponsored|paid promotion|press release)"),
    re.compile(r"(?i)^\s*(related articles|related news|you might also like|read more|more stories)"),
    re.compile(r"(?i)^\s*(all rights reserved|©|copyright|\d{4}\s+[a-z0-9 ]+\.\s+all rights)"),
    re.compile(r"(?i)^\s*(share this|tweet this|share on|comment on|email us|contact us)"),
    re.compile(r"(?i)^\s*(home|about|about us|careers|advertise|contact|help|faq|sitemap)$"),
    re.compile(r"^\s*\[(?:Image|Photo|Video)(?::.*?)?\]\s*$"),
    re.compile(r"^\s*https?://\S+\s*$"),  # bare URL lines (nav/footer links)
    re.compile(r"^\s*[-—|•]+\s*$"),
]


def strip_boilerplate(text: str) -> str:
    """Drop blank and boilerplate lines, returning one space-joined body."""
    if not text:
        return ""
    lines: list[str] = []
    for ln in text.splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        if any(p.search(stripped) for p in _BOILERPLATE_PATTERNS):
            continue
        lines.append(stripped)
    return " ".join(lines)


def content_fingerprint(text: str) -> str:
    """Stable content fingerprint that survives minor formatting changes.

    1. Strip boilerplate lines (footer, nav, ads, CTAs).
    2. Take the first 1000 chars of the remaining body.
    3. Collapse whitespace + lowercase.
    4. SHA-256.

    Returns the hex digest (deterministic for semantically identical bodies).
    """
    if not text:
        return hashlib.sha256(b"").hexdigest()
    body = strip_boilerplate(text)[:1000]
    norm = re.sub(r"\s+", " ", body).strip().lower()
    return hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()


def _recent_member(r: Any, key: str, member: str, ttl_days: int) -> bool:
    """True when *member* is in *key* inside the TTL window (lazy prune)."""
    prune_zset(r, key, ttl_days)
    score = r.zscore(key, member)
    if score is None:
        return False
    if float(score) < now_ts() - ttl_days * 86400:
        r.zrem(key, member)
        return False
    return True


def is_duplicate_content(fp: str, collection: str) -> bool:
    """True when this content fingerprint was ingested within the TTL window.

    Fail-open: Redis unavailable → False (allow through).
    """
    if not fp:
        return False
    try:
        r = get_redis()
        if r is None:
            log.debug("dedup_content_redis_unavailable allow=True")
            return False
        return _recent_member(r, f"{CONTENT_ZSET_PREFIX}{collection}", fp, CONTENT_DEDUP_TTL_DAYS)
    except Exception as e:
        log.debug("dedup_content_check_failed err=%s allow=True", e)
        return False


def mark_content_ingested(fp: str, collection: str) -> None:
    """Record a content fingerprint as ingested. Fail-open on Redis errors."""
    if not fp:
        return
    try:
        r = get_redis()
        if r is None:
            return
        r.zadd(f"{CONTENT_ZSET_PREFIX}{collection}", {fp: now_ts()})
    except Exception as e:
        log.debug("dedup_content_mark_failed err=%s", e)


def lookup_group_by_fp(fp: str, collection: str) -> str | None:
    """Return the story group_id a fingerprint was mapped to, if any."""
    try:
        r = get_redis()
        if r is None:
            return None
        val = r.hget(f"{CONTENT_GROUP_HASH_PREFIX}{collection}", fp)
        return str(val) if val else None
    except Exception as e:
        log.debug("dedup_content_group_lookup_failed err=%s", e)
        return None
