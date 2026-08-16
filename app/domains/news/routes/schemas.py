"""Shared models, scoring helpers, and row adapters for the news HTTP layer.

Split out of ``app/domains/news/router.py`` (2026-08-14). This module holds
everything with no route decorators: response/request models, the time-decay
scoring helpers (v4.0 §T28), the legacy ``crypto_news`` row adapter, and the
comment sanitization helpers (NEWS-PAGE-DESIGN.md §3).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from app.catalog.models import utcnow

# ── Time-decay scoring (per v4.0 §T28) ────────────────────────────
SOURCE_AUTHORITY: dict[str, float] = {
    # Tier 1 - major crypto-native outlets
    "coindesk": 1.0,
    "the block": 1.0,
    "decrypt": 1.0,
    "cointelegraph": 1.0,
    # Tier 2 - solid crypto coverage
    "beincrypto": 0.7,
    "u.today": 0.7,
    "crypto.news": 0.7,
    "blockworks": 0.8,
    # Tier 3 - general / RSS aggregators
    "google-crypto": 0.5,
    "reddit-crypto": 0.4,
    "twitter-crypto": 0.3,
}


def recency_decay(hours_old: float, half_life: float = 6.0) -> float:
    """Exponential decay: 1.0 at 0h, 0.5 at half_life, 0.25 at 2*half_life."""
    if hours_old < 0:
        return 1.0
    return 0.5 ** (hours_old / half_life)


def source_authority(source: str) -> float:
    s = (source or "").lower().strip()
    for key, val in SOURCE_AUTHORITY.items():
        if key in s:
            return val
    return 0.3  # unknown source


def trend_score(hours_old: float, source: str, sentiment: float | None, social_velocity: int = 0) -> float:
    """Composite trending score per v4.0 formula."""
    s_auth = source_authority(source)
    s_vel = min(2.0, 1.0 + social_velocity / 100.0)
    s_sent = 1.0 + abs(sentiment or 0.0)
    return recency_decay(hours_old) * s_auth * s_vel * s_sent


# ── Response models ────────────────────────────────────────────────
class NewsItemOut(BaseModel):
    model_config = ConfigDict(strict=False)  # accept None for any field with default

    news_id: str | None = None
    url: str | None = None
    title: str | None = None
    summary: str | None = ""
    image_url: str | None = None
    source: str | None = "unknown"
    published_at: datetime | None = None
    chains_mentioned: list[str] = Field(default_factory=list)
    tokens_mentioned: list[str] = Field(default_factory=list)
    sentiment_score: float | None = None
    bias: str | None = None
    sentiment_label: str | None = None
    factual_rating: str | None = None
    source_tier: int | None = None
    rating_confidence: float | None = None
    source_count: int | None = None
    bias_distribution: dict[str, int] = Field(default_factory=dict)
    score: float | None = None  # only set for /trending
    upvotes: int = 0  # from news_reactions (NEWS-PAGE-DESIGN.md §3)
    downvotes: int = 0  # from news_reactions
    comment_count: int = 0  # visible comments on news_comments


class NewsListResponse(BaseModel):
    items: list[NewsItemOut]
    total: int
    offset: int


class NewsAnalysisResponse(BaseModel):
    news_id: str
    analysis: str | None
    model: str | None = None
    error: str | None = None


class StorySourcesResponse(BaseModel):
    """Per-story source distribution for the bias-spectrum panel."""

    cluster_id: str
    representative_title: str = ""
    first_seen: datetime | None = None
    last_updated: datetime | None = None
    item_count: int = 0
    sentiment_avg: float = 0.0
    source_count: int = 0
    sources: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    bias_distribution: dict[str, int] = Field(default_factory=dict)
    item_ids: list[str] = Field(default_factory=list)


# ── Row adapter (legacy crypto_news → NewsItemOut) ───────────────
def _adapt_legacy_row(row: dict[str, Any]) -> NewsItemOut:
    """Convert a crypto_news row to NewsItemOut shape."""
    # published is a text field in legacy; try ISO parse
    pub_str = row.get("published") or row.get("ingested_at")
    pub_dt = utcnow()
    if pub_str:
        try:
            if isinstance(pub_str, (int, float)):
                pub_dt = datetime.fromtimestamp(float(pub_str), tz=UTC)
            else:
                # Try common formats
                for fmt in (
                    "%Y-%m-%dT%H:%M:%S.%fZ",
                    "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S",
                ):
                    try:
                        pub_dt = datetime.strptime(str(pub_str)[:19], fmt).replace(tzinfo=UTC)
                        break
                    except ValueError:
                        continue
        except (ConnectionError, OSError, TimeoutError):
            pass
    return NewsItemOut(
        news_id=row.get("id", ""),
        url=row.get("url", ""),
        title=row.get("title", ""),
        summary=(row.get("content") or "")[:500],
        source=row.get("source", "unknown"),
        published_at=pub_dt,
        chains_mentioned=[],
        tokens_mentioned=row.get("tickers") or [],
        sentiment_score=row.get("sentiment"),
    )


# ── Comments + reactions shared helpers (NEWS-PAGE-DESIGN.md §3) ──
_COMMENT_BODY_MAX = 2000
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_comment_body(body: str) -> str:
    """Strip HTML + control chars, collapse whitespace, cap at 2000 chars."""
    text = _HTML_TAG_RE.sub("", body)
    text = _CONTROL_CHAR_RE.sub("", text)
    text = " ".join(text.split())
    return text[:_COMMENT_BODY_MAX]


def _coerce_reaction(value: str | None) -> Literal["like", "dislike"] | None:
    if value in ("like", "dislike"):
        return cast("Literal['like', 'dislike']", value)
    return None


class ReactionRequest(BaseModel):
    reaction: Literal["like", "dislike"]


class ReactionsSummary(BaseModel):
    news_id: str
    like_count: int = 0
    dislike_count: int = 0
    my_reaction: Literal["like", "dislike"] | None = None


class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=_COMMENT_BODY_MAX)
    parent_id: str | None = None


class CommentOut(BaseModel):
    id: str
    news_id: str
    user_id: str
    parent_id: str | None = None
    body: str | None = None
    created_at: datetime
    edited_at: datetime | None = None
    deleted: bool = False
    like_count: int = 0
    dislike_count: int = 0
    my_reaction: Literal["like", "dislike"] | None = None
    is_mine: bool = False


class CommentListResponse(BaseModel):
    items: list[CommentOut]
    total: int


class CommentReactionResponse(BaseModel):
    comment_id: str
    like_count: int = 0
    dislike_count: int = 0
    my_reaction: Literal["like", "dislike"] | None = None


__all__ = [
    "SOURCE_AUTHORITY",
    "_COMMENT_BODY_MAX",
    "_CONTROL_CHAR_RE",
    "_HTML_TAG_RE",
    "CommentCreate",
    "CommentListResponse",
    "CommentOut",
    "CommentReactionResponse",
    "NewsAnalysisResponse",
    "NewsItemOut",
    "NewsListResponse",
    "ReactionRequest",
    "ReactionsSummary",
    "StorySourcesResponse",
    "_adapt_legacy_row",
    "_coerce_reaction",
    "_sanitize_comment_body",
    "recency_decay",
    "source_authority",
    "trend_score",
]
