"""Rank X posts with the house `source_weight`/`rank_score` shape, X-tuned.

The existing news_intel scorer combines a content score with a source weight:

    score = content * 0.6 + source_weight * 0.4

This module keeps that shape, then applies the two X-specific terms the lane
needs: a small boost for first-party security/regulation/news accounts, and an
exponential age decay (half-life 12h) because a post's news value dies fast.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "news_fallback"))

import db  # noqa: E402  (shared connection helper)

HALF_LIFE_H = 12.0
#: An id-less post (pry renders the pinned post, which carries no date) is never
#: breaking: treat it as at least this old so it cannot outrank a dated post.
UNDATED_MIN_AGE_H = 24.0
CONTENT_WEIGHT = 0.6
SOURCE_WEIGHT = 0.4
FLOOR_DECAY = 0.05
BOOST = {"security": 0.25, "regulation": 0.25, "news": 0.15, "analytics": 0.12, "project": 0.05}


@dataclass(frozen=True, slots=True)
class Ranked:
    """One scored row, ready to print."""

    news_id: str
    handle: str
    klass: str
    trust: float
    age_h: float
    score: float
    url: str
    title: str


def decay(age_hours: float) -> float:
    """Exponential age decay with a floor so nothing ranks at zero."""
    return max(FLOOR_DECAY, 0.5 ** (max(age_hours, 0.0) / HALF_LIFE_H))


def rank_score(content: float, source_weight: float, klass: str, age_hours: float) -> float:
    """House shape + X boost + age decay, clamped to 0..1."""
    base = content * CONTENT_WEIGHT + source_weight * SOURCE_WEIGHT
    return round(min(1.0, max(0.0, base * (1.0 + BOOST.get(klass, 0.0)) * decay(age_hours))), 4)


def _age(published: datetime | None, now: datetime) -> float:
    if published is None:
        return 0.0
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    return max(0.0, (now - published).total_seconds() / 3600.0)


def top(limit: int = 10) -> list[Ranked]:
    """Score every stored X row and return the best `limit`."""
    now = datetime.now(UTC)
    query = """
        SELECT news_id, source, category, source_trust, body_quality_score,
               published_at, url, title, tweet_id
        FROM news_items
        WHERE source_type = 'x'
        ORDER BY published_at DESC NULLS LAST
        LIMIT 2000
    """
    out: list[Ranked] = []
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            for row in cur.fetchall():
                news_id, handle, klass, trust, quality, published, url, title, tweet_id = row
                age_h = _age(published, now)
                if tweet_id is None:
                    age_h = max(age_h, UNDATED_MIN_AGE_H)
                content = float(quality or 0) / 100.0
                out.append(
                    Ranked(
                        news_id=str(news_id),
                        handle=str(handle),
                        klass=str(klass or "media"),
                        trust=float(trust or 0.0),
                        age_h=round(age_h, 2),
                        score=rank_score(
                            content, float(trust or 0.0), str(klass or "media"), age_h
                        ),
                        url=str(url),
                        title=str(title or "")[:70],
                    )
                )
    out.sort(key=lambda row: row.score, reverse=True)
    return out[:limit]


def render(rows: list[Ranked]) -> str:
    """Fixed-width table for the CLI report."""
    lines = ["rank  score   class       trust  age_h  handle             title"]
    for i, row in enumerate(rows, 1):
        lines.append(
            f"{i:>4}  {row.score:<6}  {row.klass:<10}  {row.trust:<5}  "
            f"{row.age_h:>5}  {row.handle[:18]:<18} {row.title}"
        )
    return "\n".join(lines)
