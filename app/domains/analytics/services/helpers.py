"""Helper functions for analytics computation."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from typing import Any


def hash_ip(identifier: str) -> str:
    """Create a privacy-safe hash of an IP or identifier."""
    return hashlib.sha256(identifier.encode()).hexdigest()[:16]


def empty_view_summary(period_days: int) -> dict[str, Any]:
    return {
        "total_views": 0,
        "unique_viewers": 0,
        "total_watch_time_minutes": 0.0,
        "average_view_duration_seconds": 0.0,
        "median_view_duration_seconds": 0.0,
        "view_through_rate": 0.0,
        "scroll_by_rate": 0.0,
        "replay_views": 0,
        "peak_concurrent_viewers": 0,
        "drop_off_points": [],
        "daily_trend": [],
        "period": f"{period_days}d",
    }


def empty_engagement_summary(period_days: int) -> dict[str, Any]:
    return {
        "total_engagements": 0,
        "unique_engagers": 0,
        "likes": 0,
        "unique_likers": 0,
        "comments": 0,
        "unique_commenters": 0,
        "comment_sentiment_positive": 0,
        "comment_sentiment_neutral": 0,
        "comment_sentiment_negative": 0,
        "average_sentiment_score": 0.0,
        "reposts": 0,
        "total_tips_usd": 0.0,
        "total_tips_token": 0.0,
        "average_tip_usd": 0.0,
        "tip_count": 0,
        "tip_velocity": 0.0,
        "top_tip_token": "",
        "followers_gained": 0,
        "click_through_rate": 0.0,
        "total_clicks": 0,
        "saves": 0,
        "save_rate": 0.0,
        "average_read_time_seconds": 0.0,
        "read_completion_rate": 0.0,
        "period": f"{period_days}d",
    }


def empty_audience_summary() -> dict[str, Any]:
    return {
        "total_followers": 0,
        "followers_gained_30d": 0,
        "followers_lost_30d": 0,
        "net_growth_30d": 0,
        "churn_rate_30d": 0.0,
        "growth_rate_30d": 0.0,
        "protocol_breakdown": {},
        "top_interests": [],
        "top_chains": [],
        "most_engaged_followers": [],
        "top_tippers": [],
        "growth_trend": [],
        "top_acquisition_sources": [],
    }


def empty_growth() -> dict[str, Any]:
    return {
        "daily_growth": [],
        "weekly_growth": [],
        "monthly_growth": [],
        "protocol_growth": {},
        "acquisition_channels": [],
    }


def get_best_hour_str(views: list[dict[str, Any]]) -> str:
    """Find the hour with most views and return it as a string."""
    hour_counts: dict[int, int] = defaultdict(int)
    for v in views:
        created = v.get("created_at", "")
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            hour_counts[dt.hour] += 1
        except (ValueError, TypeError):
            pass
    if not hour_counts:
        return "14:00 UTC"
    best_hour = max(hour_counts, key=hour_counts.get)
    return f"{best_hour:02d}:00 UTC"


def infer_best_content_type(views: list[dict[str, Any]]) -> str:
    """Infer the best-performing content type from views."""
    if not views:
        return "micro"
    with_watch_time = [v for v in views if (v.get("watch_time_seconds") or 0) > 30]
    if len(with_watch_time) > len(views) * 0.5:
        return "video"
    deep_scroll = [v for v in views if (v.get("scroll_depth_pct") or 0) > 50]
    if len(deep_scroll) > len(views) * 0.3:
        return "article"
    return "micro"


def compute_metric_by_group(
    posts: list[dict[str, Any]],
    views_by_post: dict[str, list[dict[str, Any]]],
    engagements_by_post: dict[str, list[dict[str, Any]]],
    key_fn: Any,
    metric: str = "engagement_rate",
) -> dict[str, float]:
    """Compute average metric grouped by key_fn."""
    groups: dict[str, list[float]] = defaultdict(list)

    for post in posts:
        post_id = post.get("id", "")
        group = key_fn(post)
        p_views = views_by_post.get(post_id, [])
        p_engagements = engagements_by_post.get(post_id, [])

        if metric == "engagement_rate":
            value = (len(p_engagements) / max(len(p_views), 1)) * 100
        elif metric == "tips_usd":
            value = sum(e.get("tip_amount_usd", 0) or 0 for e in p_engagements)
        elif metric == "views":
            value = len(p_views)
        else:
            value = 0.0

        groups[group].append(value)

    return {group: sum(vals) / len(vals) for group, vals in groups.items() if vals}


def compute_metric_by_hour(
    posts: list[dict[str, Any]],
    views_by_post: dict[str, list[dict[str, Any]]],
    engagements_by_post: dict[str, list[dict[str, Any]]],
) -> dict[int, float]:
    """Compute average views per posting hour."""
    hour_views: dict[int, list[int]] = defaultdict(list)

    for post in posts:
        post_id = post.get("id", "")
        published = post.get("published_at", "")
        if not published:
            continue
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            hour = dt.hour
        except (ValueError, TypeError):
            continue

        p_views = views_by_post.get(post_id, [])
        hour_views[hour].append(len(p_views))

    return {hour: sum(vals) / len(vals) for hour, vals in hour_views.items() if vals}


def compute_protocol_peak_times(
    views_by_post: dict[str, list[dict[str, Any]]],
) -> dict[str, str]:
    """Find the peak activity hour for each protocol."""
    proto_hours: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    for post_id, views in views_by_post.items():
        for v in views:
            created = v.get("created_at", "")
            if not created:
                continue
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                hour = dt.hour
            except (ValueError, TypeError):
                continue
            proto = v.get("protocol", "unknown")
            proto_hours[proto][hour] += 1

    result = {}
    for proto, hours in proto_hours.items():
        if hours:
            peak_hour = max(hours, key=hours.get)
            result[proto] = f"{peak_hour:02d}"
    return result


def avg_view_duration(
    posts: list[dict[str, Any]],
    views_by_post: dict[str, list[dict[str, Any]]],
) -> float:
    """Compute average view duration for a group of posts."""
    total_duration = 0.0
    total_views = 0
    for p in posts:
        pid = p.get("id", "")
        for v in views_by_post.get(pid, []):
            total_duration += v.get("watch_time_seconds", 0) or 0
            total_views += 1
    return total_duration / max(total_views, 1)


def confidence(sample_size: int) -> float:
    """Statistical confidence based on sample size."""
    if sample_size >= 100:
        return 0.95
    if sample_size >= 50:
        return 0.85
    if sample_size >= 20:
        return 0.7
    if sample_size >= 10:
        return 0.5
    return 0.3


def extract_top_category(tags_str: str) -> str:
    """Extract the first meaningful category from tags."""
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
    return tags[0] if tags else "general"


def aggregate_to_weekly(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate daily data points into weekly buckets."""
    weekly: dict[str, float] = defaultdict(float)
    for d in daily:
        try:
            dt = datetime.fromisoformat(d["date"])
            week = dt.strftime("%Y-W%W")
            weekly[week] += d["value"]
        except (ValueError, TypeError):
            pass
    return [{"date": w, "value": v, "label": w} for w, v in sorted(weekly.items())]


def aggregate_to_monthly(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate daily data points into monthly buckets."""
    monthly: dict[str, float] = defaultdict(float)
    for d in daily:
        month = d["date"][:7]
        monthly[month] += d["value"]
    return [{"date": m, "value": v, "label": m} for m, v in sorted(monthly.items())]
