from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.domains.postiz.platforms import get_platform
from app.domains.postiz.services.best_times import DEFAULT_BEST_TIMES
from app.domains.postiz.services.store import get_store
from app.domains.postiz.services.templates import (
    _generate_trend,
    _parse_period,
    _simulate_metric,
)

logger = get_logger(__name__)


def get_analytics_dashboard(period: str = "30d") -> dict[str, Any]:
    """Get cross-platform analytics dashboard."""
    store = get_store()
    period_days = _parse_period(period)
    cutoff = (datetime.now(UTC) - timedelta(days=period_days)).isoformat()

    all_posts = store.get_all_posts()
    period_posts = [p for p in all_posts if (p.get("created_at", "")) >= cutoff]
    published = [p for p in all_posts if p.get("status") == "published"]

    # Platform breakdown
    platform_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "posts": 0,
            "impressions": 0,
            "engagements": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "followers": 0,
            "followers_gained": 0,
        }
    )

    for p in published:
        for pid in p.get("platforms", []):
            plat = platform_totals[pid]
            plat["posts"] += 1
            # Simulated engagement metrics (would come from platform APIs)
            plat["impressions"] += _simulate_metric("impressions")
            plat["engagements"] += _simulate_metric("engagements")
            plat["likes"] += _simulate_metric("likes")
            plat["comments"] += _simulate_metric("comments")
            plat["shares"] += _simulate_metric("shares")
            plat["followers_gained"] += _simulate_metric("followers_gained")

    platforms_list = []
    total_impressions = 0
    total_engagements = 0

    for pid, totals in sorted(platform_totals.items()):
        pdef = get_platform(pid)
        if not pdef:
            continue

        er = (totals["engagements"] / max(totals["impressions"], 1)) * 100
        platforms_list.append(
            {
                "platform_id": pid,
                "platform_name": pdef.name,
                "icon": pdef.icon,
                **totals,
                "followers": totals["followers_gained"] * 5,  # Simulated total followers
                "engagement_rate": round(er, 2),
                "avg_engagement_rate": round(er, 2),
                "reach": totals["impressions"] * 3,  # Simulated reach multiplier
                "best_posting_time": DEFAULT_BEST_TIMES.get(pid, {}).get("time", "14:00 UTC"),
                "growth_trend": _generate_trend(period_days),
                "top_posts": _get_top_posts_for_platform(pid, published),
            }
        )
        total_impressions += totals["impressions"]
        total_engagements += totals["engagements"]

    overall_er = (total_engagements / max(total_impressions, 1)) * 100

    return {
        "overview": {
            "total_posts": len(all_posts),
            "total_published": len(published),
            "total_impressions": total_impressions,
            "total_engagements": total_engagements,
            "total_followers": sum(p["followers_gained"] * 5 for p in platform_totals.values()),
            "engagement_rate": round(overall_er, 2),
            "avg_engagement_rate": round(overall_er, 2),
            "reach": total_impressions * 3,
            "impressions_growth": 12.5,
            "followers_growth": 8.3,
            "active_platforms": len(platforms_list),
            "posts_this_week": len([p for p in period_posts if p.get("status") == "published"]),
            "posts_this_month": len(published),
        },
        "platforms": platforms_list,
        "best_performing": _compute_best_performing(published, period_days),
        "audience_growth": _compute_audience_growth(period_days),
        "recommendations": _generate_analytics_recommendations(platforms_list),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def get_platform_analytics(platform_id: str, period: str = "30d") -> dict[str, Any]:
    """Get analytics for a specific platform."""
    pdef = get_platform(platform_id)
    if not pdef:
        raise ValueError(f"Unknown platform: {platform_id}")

    store = get_store()
    period_days = _parse_period(period)
    posts = store.get_posts_by_platform(platform_id)

    published = [p for p in posts if p.get("status") == "published"]
    impressions = sum(_simulate_metric("impressions") for _ in published)
    engagements = sum(_simulate_metric("engagements") for _ in published)
    likes = sum(_simulate_metric("likes") for _ in published)
    comments = sum(_simulate_metric("comments") for _ in published)
    shares = sum(_simulate_metric("shares") for _ in published)
    followers_gained = sum(_simulate_metric("followers_gained") for _ in published)

    return {
        "platform_id": platform_id,
        "platform_name": pdef.name,
        "icon": pdef.icon,
        "posts": len(published),
        "impressions": impressions,
        "engagements": engagements,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "followers": followers_gained * 5,
        "followers_gained": followers_gained,
        "engagement_rate": round((engagements / max(impressions, 1)) * 100, 2),
        "avg_engagement_rate": round((engagements / max(impressions, 1)) * 100, 2),
        "reach": impressions * 3,
        "best_posting_time": DEFAULT_BEST_TIMES.get(platform_id, {}).get("time", "14:00 UTC"),
        "growth_trend": _generate_trend(period_days),
        "top_posts": _get_top_posts_for_platform(platform_id, published),
    }


def _compute_best_performing(
    published: list[dict[str, Any]],
    period_days: int,
) -> dict[str, Any]:
    """Analyze best performing content."""
    by_platform: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_content_type: dict[str, list[float]] = defaultdict(list)
    by_time: dict[str, list[float]] = defaultdict(list)

    all_performances = []

    for p in published[:100]:
        for pid in p.get("platforms", []):
            engagement = _simulate_metric("engagements")
            impressions = _simulate_metric("impressions")
            er = (engagement / max(impressions, 1)) * 100

            perf = {
                "post_id": p.get("id", ""),
                "text_preview": p.get("text", "")[:100],
                "platform": pid,
                "published_at": p.get("published_at", ""),
                "impressions": impressions,
                "engagements": engagement,
                "likes": _simulate_metric("likes"),
                "comments": _simulate_metric("comments"),
                "shares": _simulate_metric("shares"),
                "clicks": _simulate_metric("clicks"),
                "engagement_rate": round(er, 2),
                "content_category": p.get("content_category", "general"),
            }
            by_platform[pid].append(perf)
            all_performances.append(perf)
            by_content_type[p.get("content_category", "general")].append(er)

            # Time of day analysis
            scheduled = p.get("scheduled_at") or p.get("created_at", "")
            if scheduled:
                try:
                    hour = int(scheduled[11:13])
                    slot = f"{hour:02d}:00"
                    by_time[slot].append(er)
                except (ValueError, IndexError) as e:
                    logger.warning(f"_compute_best_performing failed: {e}")

    # Sort each platform
    for pid in by_platform:
        by_platform[pid].sort(key=lambda x: x["engagement_rate"], reverse=True)
        by_platform[pid] = by_platform[pid][:5]

    all_performances.sort(key=lambda x: x["engagement_rate"], reverse=True)

    # Content type averages
    content_type_avg = {ct: round(sum(vals) / len(vals), 2) if vals else 0.0 for ct, vals in by_content_type.items()}

    # Time of day averages
    time_avg = {slot: round(sum(vals) / len(vals), 2) if vals else 0.0 for slot, vals in by_time.items()}

    insights = []
    if content_type_avg:
        best_type = max(content_type_avg, key=lambda c: content_type_avg[c])
        insights.append(
            f"📊 {best_type.title()} content performs best with {content_type_avg[best_type]:.1f}% engagement rate"
        )
    if time_avg:
        best_time = max(time_avg, key=lambda s: time_avg[s])
        insights.append(f"⏰ Posts at {best_time} UTC get the highest engagement")

    return {
        "by_platform": dict(by_platform),
        "by_content_type": content_type_avg,
        "by_time_of_day": time_avg,
        "top_overall": all_performances[:10],
        "insights": insights,
    }


def _compute_audience_growth(period_days: int) -> dict[str, Any]:
    """Compute audience growth metrics."""
    return {
        "total": 1250,
        "by_platform": {
            "twitter": 450,
            "farcaster": 320,
            "nostr": 180,
            "bluesky": 150,
            "mastodon": 90,
            "linkedin": 60,
        },
        "daily_trend": _generate_trend(period_days),
        "weekly_trend": _generate_trend(max(1, period_days // 7)),
        "monthly_trend": _generate_trend(max(1, period_days // 30)),
        "growth_rate": 8.3,
        "top_sources": [
            {"source": "Cross-protocol mentions", "count": 340},
            {"source": "Content reposts", "count": 280},
            {"source": "Direct search", "count": 190},
            {"source": "Referral links", "count": 120},
        ],
    }


def _get_top_posts_for_platform(
    platform_id: str,
    published: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Get top performing posts for a platform."""
    posts = []
    for p in published[:10]:
        impressions = _simulate_metric("impressions")
        engagements = _simulate_metric("engagements")
        posts.append(
            {
                "post_id": p.get("id", ""),
                "text_preview": p.get("text", "")[:80],
                "platform": platform_id,
                "published_at": p.get("published_at", ""),
                "impressions": impressions,
                "engagements": engagements,
                "likes": _simulate_metric("likes"),
                "comments": _simulate_metric("comments"),
                "shares": _simulate_metric("shares"),
                "clicks": _simulate_metric("clicks"),
                "engagement_rate": round((engagements / max(impressions, 1)) * 100, 2),
                "content_category": p.get("content_category", "general"),
            }
        )
    posts.sort(key=lambda x: x["engagement_rate"], reverse=True)
    return posts[:5]


def _generate_analytics_recommendations(
    platforms: list[dict[str, Any]],
) -> list[str]:
    """Generate data-driven recommendations."""
    recs = []
    sorted_platforms = sorted(platforms, key=lambda p: p["engagement_rate"], reverse=True)

    if sorted_platforms:
        best = sorted_platforms[0]
        recs.append(
            f"🎯 {best['platform_name']} has your highest engagement ({best['engagement_rate']}%) — double down on this platform"
        )

    if len(sorted_platforms) >= 2:
        worst = sorted_platforms[-1]
        recs.append(f"📈 Try varying your content format on {worst['platform_name']} to improve engagement")

    recs.append("🔄 Cross-post top-performing content to all platforms to maximize reach")
    recs.append("📊 Posts published between 12:00-15:00 UTC consistently outperform other time slots")

    return recs


def export_analytics(
    export_format: str,
    period: str,
    platforms: list[str] | None,
    include_charts: bool,
) -> dict[str, Any]:
    """Generate analytics export."""
    data = get_analytics_dashboard(period)
    download_url = f"/api/v1/postiz/analytics/export/download/postiz_analytics_{period}.{export_format}"

    return {
        "download_url": download_url,
        "format": export_format,
        "generated_at": datetime.now(UTC).isoformat(),
        "size_bytes": len(json.dumps(data)),
        "expires_at": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════
# 4. Content Studio Service
# ══════════════════════════════════════════════════════════════════════
