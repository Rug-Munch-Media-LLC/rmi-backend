"""Engagement analytics — likes, comments, tips, cross-protocol."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.core.logging import get_logger
from app.domains.analytics.services.constants import SUPPORTED_PROTOCOLS
from app.domains.analytics.services.helpers import (
    empty_engagement_summary,
    get_best_hour_str,
    infer_best_content_type,
)
from app.domains.analytics.services.view_analytics import compute_view_analytics

logger = get_logger(__name__)


def compute_engagement_analytics(
    engagements: list[dict[str, Any]],
    period_days: int = 7,
) -> dict[str, Any]:
    """Compute engagement analytics from raw events."""
    if not engagements:
        return empty_engagement_summary(period_days)

    total = len(engagements)
    unique_engagers: set[str] = set()
    likes = 0
    unique_likers: set[str] = set()
    comments = 0
    unique_commenters: set[str] = set()
    sentiment_counts = {"positive": 0, "neutral": 0, "negative": 0}
    sentiment_scores: list[float] = []
    reposts = 0
    total_tips_usd = 0.0
    total_tips_token = 0.0
    tip_count = 0
    tip_tokens: dict[str, float] = defaultdict(float)
    followers_gained = 0
    total_clicks = 0
    saves = 0

    for e in engagements:
        fp = e.get("viewer_fingerprint", "")
        if fp:
            unique_engagers.add(fp)

        etype = e.get("engagement_type", "")
        if etype == "like":
            likes += 1
            if fp:
                unique_likers.add(fp)
        elif etype == "comment":
            comments += 1
            if fp:
                unique_commenters.add(fp)
            sent = e.get("comment_sentiment", "neutral")
            sentiment_counts[sent] = sentiment_counts.get(sent, 0) + 1
            sentiment_scores.append(e.get("comment_sentiment_score", 0.0))
        elif etype == "repost":
            reposts += 1
        elif etype == "tip":
            tip_count += 1
            total_tips_usd += e.get("tip_amount_usd", 0) or 0
            total_tips_token += e.get("tip_amount_token", 0) or 0
            token = e.get("tip_token", "unknown")
            tip_tokens[token] += e.get("tip_amount_usd", 0) or 0
        elif etype == "follow":
            followers_gained += 1
        elif etype == "click":
            total_clicks += 1
        elif etype in ("save", "bookmark"):
            saves += 1

    avg_tip = total_tips_usd / tip_count if tip_count else 0.0
    avg_sentiment = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0.0
    top_tip_token = max(tip_tokens, key=tip_tokens.get) if tip_tokens else ""

    return {
        "total_engagements": total,
        "unique_engagers": len(unique_engagers),
        "likes": likes,
        "unique_likers": len(unique_likers),
        "comments": comments,
        "unique_commenters": len(unique_commenters),
        "comment_sentiment_positive": sentiment_counts.get("positive", 0),
        "comment_sentiment_neutral": sentiment_counts.get("neutral", 0),
        "comment_sentiment_negative": sentiment_counts.get("negative", 0),
        "average_sentiment_score": round(avg_sentiment, 4),
        "reposts": reposts,
        "total_tips_usd": round(total_tips_usd, 2),
        "total_tips_token": round(total_tips_token, 4),
        "average_tip_usd": round(avg_tip, 2),
        "tip_count": tip_count,
        "tip_velocity": round(tip_count / max(period_days, 1), 2),
        "top_tip_token": top_tip_token,
        "followers_gained": followers_gained,
        "click_through_rate": 0.0,
        "total_clicks": total_clicks,
        "saves": saves,
        "save_rate": 0.0,
        "average_read_time_seconds": 0.0,
        "read_completion_rate": 0.0,
        "period": f"{period_days}d",
    }


def compute_cross_protocol_analytics(
    views_by_protocol: dict[str, list[dict[str, Any]]],
    engagements_by_protocol: dict[str, list[dict[str, Any]]],
    period_days: int = 7,
) -> dict[str, Any]:
    """Compare performance across all protocols.

    Shows which protocol gives the best reach, engagement, and tips.
    """
    comparisons: list[dict[str, Any]] = []
    best_reach = ("", 0)
    best_engagement = ("", 0.0)
    best_tips = ("", 0.0)
    total_views_all = 0
    all_unique_viewers: set[str] = set()

    for protocol in SUPPORTED_PROTOCOLS:
        p_views = views_by_protocol.get(protocol, [])
        p_engagements = engagements_by_protocol.get(protocol, [])

        if not p_views and not p_engagements:
            continue

        v_summary = compute_view_analytics(p_views, period_days)
        e_summary = compute_engagement_analytics(p_engagements, period_days)

        views_count = v_summary["total_views"]
        unique = v_summary["unique_viewers"]
        eng_count = e_summary["total_engagements"]
        tips_amount = e_summary["total_tips_usd"]

        total_views_all += views_count
        for v in p_views:
            fp = v.get("viewer_fingerprint", "")
            if fp:
                all_unique_viewers.add(fp)

        engagement_rate = (eng_count / views_count * 100) if views_count else 0.0

        reach_score = min(100.0, (unique / 1000) * 100)
        engagement_score = min(100.0, engagement_rate * 10)

        comparisons.append(
            {
                "protocol": protocol,
                "views": views_count,
                "unique_viewers": unique,
                "total_watch_time_minutes": v_summary["total_watch_time_minutes"],
                "average_view_duration_seconds": v_summary["average_view_duration_seconds"],
                "engagements": eng_count,
                "likes": e_summary["likes"],
                "comments": e_summary["comments"],
                "reposts": e_summary["reposts"],
                "tips_usd": tips_amount,
                "followers_gained": e_summary["followers_gained"],
                "engagement_rate": round(engagement_rate, 2),
                "best_posting_time": get_best_hour_str(p_views),
                "best_content_type": infer_best_content_type(p_views),
                "reach_score": round(reach_score, 1),
                "engagement_score": round(engagement_score, 1),
            }
        )

        if unique > best_reach[1]:
            best_reach = (protocol, unique)
        if engagement_rate > best_engagement[1]:
            best_engagement = (protocol, engagement_rate)
        if tips_amount > best_tips[1]:
            best_tips = (protocol, tips_amount)

    return {
        "comparisons": comparisons,
        "best_protocol_for_reach": best_reach[0],
        "best_protocol_for_engagement": best_engagement[0],
        "best_protocol_for_tips": best_tips[0],
        "total_cross_protocol_views": total_views_all,
        "total_unique_viewers": len(all_unique_viewers),
        "period": f"{period_days}d",
    }
