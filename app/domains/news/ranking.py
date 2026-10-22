"""Transparent feed ranking + cross-source collapse for the news feed.

Score formula (deterministic, no LLM, no per-row embedding):

    score = recency_decay * source_trust * category_interest * engagement

Every factor is returned in ``score_components`` so any row's rank can be
explained from the API response alone. Duplicate stories are collapsed by
normalised-title key; the earliest authoritative source wins and every other
outlet is listed under ``also_covered_by``.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

HALF_LIFE_HOURS = 8.0  # recency half-life: weight halves every 8h

# Editorial interest by category (1.0 = neutral baseline).
CATEGORY_INTEREST: dict[str, float] = {
    "exploit": 1.30,
    "scam": 1.25,
    "regulation": 1.15,
    "research": 1.05,
    "market": 1.00,
    "news": 0.90,
    "shilling": 0.35,
}
DEFAULT_CATEGORY_INTEREST = 0.90
DEFAULT_SOURCE_TRUST = 0.45

_HTML_TAG = re.compile(r"<[^>]*>")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def recency_decay(hours_old: float, half_life: float = HALF_LIFE_HOURS) -> float:
    """1.0 at 0h, 0.5 at half_life, 0.25 at 2x half_life. Future items clamp to 1.0."""
    if hours_old <= 0:
        return 1.0
    return 0.5 ** (hours_old / half_life)


def title_key(title: str) -> str:
    """Normalised title key used for duplicate detection (lowercase alnum only)."""
    return _NON_ALNUM.sub("", _HTML_TAG.sub("", (title or "").lower()))


def category_interest(category: str | None) -> float:
    return CATEGORY_INTEREST.get((category or "").strip().lower(), DEFAULT_CATEGORY_INTEREST)


def source_trust_factor(source_trust: Any) -> float:
    """Clamp the stored trust (0..1); missing trust falls back to the baseline."""
    try:
        val = float(source_trust)
    except (TypeError, ValueError):
        return DEFAULT_SOURCE_TRUST
    if math.isnan(val) or val <= 0:
        return DEFAULT_SOURCE_TRUST
    return min(1.0, max(0.05, val))


def engagement_multiplier(upvotes: int = 0, downvotes: int = 0, comments: int = 0) -> float:
    """Log-scaled reader signal. 1.0 with no engagement, capped at 1.6."""
    net = max(0, int(upvotes) - int(downvotes)) + int(comments)
    return min(1.6, 1.0 + 0.15 * math.log1p(net))


def score_row(row: dict[str, Any], now: datetime) -> tuple[float, dict[str, float]]:
    """Score one row and return (score, components)."""
    published = row.get("published_at")
    hours_old = 0.0
    if isinstance(published, datetime):
        hours_old = max(0.0, (now - published).total_seconds() / 3600.0)
    components = {
        "recency_decay": round(recency_decay(hours_old), 4),
        "source_trust": round(source_trust_factor(row.get("source_trust")), 4),
        "category_interest": round(category_interest(row.get("category")), 4),
        "engagement": round(
            engagement_multiplier(
                row.get("upvotes") or 0,
                row.get("downvotes") or 0,
                row.get("comment_count") or 0,
            ),
            4,
        ),
    }
    score = 1.0
    for value in components.values():
        score *= value
    return round(score, 6), components


def _story_key(row: dict[str, Any]) -> str:
    key = title_key(row.get("title") or "")
    return key or str(row.get("url") or row.get("news_id") or "")


def _authority(row: dict[str, Any]) -> tuple[float, float]:
    """Ranking key for a story's representative: trust first, then earliest."""
    published = row.get("published_at")
    ts = published.timestamp() if isinstance(published, datetime) else float("inf")
    return (source_trust_factor(row.get("source_trust")), -ts)


def collapse_duplicates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse near-identical stories, keeping the most authoritative copy.

    Each surviving row gains ``also_covered_by`` (other outlets, capped at 5)
    and ``source_count`` (distinct sources on the story).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not title_key(row.get("title") or ""):
            continue  # empty/junk title: never a feed story
        groups.setdefault(_story_key(row), []).append(row)

    collapsed: list[dict[str, Any]] = []
    for members in groups.values():
        members.sort(key=_authority, reverse=True)
        lead = dict(members[0])
        seen: set[str] = set()
        also: list[dict[str, Any]] = []
        for other in members[1:]:
            src = str(other.get("source") or "")
            if src and src != str(lead.get("source") or "") and src not in seen:
                seen.add(src)
                also.append(
                    {
                        "source": src,
                        "url": other.get("url") or "",
                        "published_at": other.get("published_at").isoformat()
                        if isinstance(other.get("published_at"), datetime)
                        else None,
                    }
                )
        lead["also_covered_by"] = also[:5]
        lead["source_count"] = len({str(m.get("source") or "").strip() for m in members} - {""})
        collapsed.append(lead)
    return collapsed


def apply_source_diversity(rows: list[dict[str, Any]], max_per_source: int = 3) -> list[dict[str, Any]]:
    """Cap source dominance: at most N stories per source (a regulator firehose
    must not fill the feed); capped stories stay discoverable deeper down."""
    counts: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for row in rows:
        src = str(row.get("source") or "unknown").lower()
        if counts.get(src, 0) >= max_per_source:
            continue
        counts[src] = counts.get(src, 0) + 1
        kept.append(row)
    return kept


def rank_items(
    rows: list[dict[str, Any]],
    now: datetime,
    *,
    collapse: bool = True,
    max_per_source: int = 3,
) -> list[dict[str, Any]]:
    """Primary entry point: score, collapse duplicates, diversify, sort best-first."""
    candidates = collapse_duplicates(rows) if collapse else [dict(r) for r in rows]
    for row in candidates:
        row["score"], row["score_components"] = score_row(row, now)
    candidates.sort(key=lambda r: (r["score"], str(r.get("news_id") or "")), reverse=True)
    return apply_source_diversity(candidates, max_per_source) if max_per_source else candidates


def news_item_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Map a ranked row onto the ``NewsItemOut`` keyword arguments."""
    return {
        "news_id": row.get("news_id"),
        "url": row.get("url") or "",
        "title": row.get("title") or "",
        "summary": row.get("summary") or "",
        "image_url": row.get("image_url"),
        "source": row.get("source") or "unknown",
        "published_at": row.get("published_at"),
        "chains_mentioned": list(row.get("chains_mentioned") or []),
        "tokens_mentioned": list(row.get("tokens_mentioned") or []),
        "sentiment_score": row.get("sentiment_score"),
        "bias": row.get("bias"),
        "sentiment_label": row.get("sentiment_label"),
        "factual_rating": row.get("factual_rating"),
        "source_tier": row.get("source_tier"),
        "rating_confidence": row.get("rating_confidence"),
        "upvotes": row.get("upvotes") or 0,
        "downvotes": row.get("downvotes") or 0,
        "comment_count": row.get("comment_count") or 0,
        "source_count": row.get("source_count") or 1,
        "also_covered_by": row.get("also_covered_by") or [],
        "score": row.get("score"),
        "score_components": row.get("score_components") or {},
    }
