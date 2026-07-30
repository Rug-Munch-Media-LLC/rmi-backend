"""Content insights — AI-driven pattern detection for improvement suggestions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.analytics.services.helpers import (
    avg_view_duration,
    compute_metric_by_group,
    compute_metric_by_hour,
    compute_protocol_peak_times,
    confidence,
    extract_top_category,
)

logger = get_logger(__name__)


def compute_content_insights(
    posts: list[dict[str, Any]],
    views_by_post: dict[str, list[dict[str, Any]]],
    engagements_by_post: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Generate data-driven content improvement insights.

    Detects patterns like:
    - "Posts with images get 2.3x more engagement"
    - "60-second shorts perform 3x better than 30-second"
    - "Posting at 2pm UTC gets 40% more views"
    """
    insights: list[dict[str, Any]] = []

    if len(posts) < 3:
        return {
            "insights": [
                {
                    "category": "recommendation",
                    "insight": "Publish at least 3 posts to unlock personalized content insights",
                    "metric": "post_count",
                    "improvement_factor": 0.0,
                    "confidence": 1.0,
                    "sample_size": len(posts),
                }
            ],
            "top_recommendations": ["Publish more content to get personalized insights"],
            "generated_at": datetime.now(UTC).isoformat(),
        }

    # 1. Media type analysis
    media_engagement = compute_metric_by_group(
        posts,
        views_by_post,
        engagements_by_post,
        key_fn=lambda p: "with_images" if p.get("has_images") else "without_images",
        metric="engagement_rate",
    )
    if media_engagement and len(media_engagement) >= 2:
        with_img = media_engagement.get("with_images", 0)
        without = media_engagement.get("without_images", 0)
        if without > 0 and with_img > 0:
            factor = round(with_img / without, 1)
            if factor != 1.0:
                insights.append(
                    {
                        "category": "media_type",
                        "insight": f"Your posts with images get {factor}x more engagement",
                        "metric": "engagement_rate",
                        "improvement_factor": factor,
                        "confidence": confidence(len(posts)),
                        "sample_size": len(posts),
                    }
                )

    # 2. Video length analysis
    video_posts = [p for p in posts if p.get("has_video") and p.get("video_duration_seconds", 0) > 0]
    if len(video_posts) >= 3:
        short = [p for p in video_posts if p.get("video_duration_seconds", 0) <= 60]
        long_p = [p for p in video_posts if p.get("video_duration_seconds", 0) > 60]
        if short and long_p:
            short_perf = avg_view_duration(short, views_by_post)
            long_perf = avg_view_duration(long_p, views_by_post)
            if short_perf > 0 and long_perf > 0:
                factor = round(short_perf / long_perf, 1)
                insights.append(
                    {
                        "category": "video_length",
                        "insight": f"Your 60-second shorts perform {factor}x better than longer videos"
                        if factor > 1
                        else "Your longer videos (>60s) outperform shorts in watch time",
                        "metric": "average_view_duration_seconds",
                        "improvement_factor": factor,
                        "confidence": confidence(len(video_posts)),
                        "sample_size": len(video_posts),
                    }
                )

    # 3. Posting time analysis
    time_slots = compute_metric_by_hour(posts, views_by_post, engagements_by_post)
    if time_slots:
        best_hour = max(time_slots, key=time_slots.get)
        avg_all = sum(time_slots.values()) / len(time_slots) if time_slots else 0
        if avg_all > 0 and time_slots[best_hour] > avg_all:
            pct_improvement = round((time_slots[best_hour] - avg_all) / avg_all * 100)
            hour_str = f"{best_hour}:00 UTC"
            insights.append(
                {
                    "category": "posting_time",
                    "insight": f"Posting at {hour_str} gets {pct_improvement}% more views",
                    "metric": "views",
                    "improvement_factor": round(time_slots[best_hour] / avg_all, 1) if avg_all > 0 else 1.0,
                    "confidence": confidence(len(posts)),
                    "sample_size": len(posts),
                }
            )

    # 4. Content category analysis
    category_perf = compute_metric_by_group(
        posts,
        views_by_post,
        engagements_by_post,
        key_fn=lambda p: extract_top_category(p.get("tags", "")),
        metric="tips_usd",
    )
    if category_perf and len(category_perf) >= 2:
        top_cat = max(category_perf, key=category_perf.get)
        bottom_cat = min(category_perf, key=category_perf.get)
        if category_perf[bottom_cat] > 0:
            factor = round(category_perf[top_cat] / category_perf[bottom_cat], 1)
            if factor > 1.5:
                insights.append(
                    {
                        "category": "content_category",
                        "insight": f"Your {top_cat} content gets {factor}x more tips than {bottom_cat} content",
                        "metric": "tips_usd",
                        "improvement_factor": factor,
                        "confidence": confidence(len(posts)),
                        "sample_size": len(posts),
                    }
                )

    # 5. Protocol timing analysis
    protocol_times = compute_protocol_peak_times(views_by_post)
    for proto, hour in protocol_times.items():
        insights.append(
            {
                "category": "protocol_timing",
                "insight": f"Your audience is most active on {proto.title()} at {hour}:00",
                "metric": "views",
                "improvement_factor": 1.0,
                "confidence": 0.7,
                "sample_size": len(
                    [v for v_list in views_by_post.values() for v in v_list if v.get("protocol") == proto]
                ),
            }
        )

    # 6. Top recommendation (video length)
    if video_posts:
        top_videos = sorted(
            video_posts,
            key=lambda p: len(views_by_post.get(p.get("id", ""), [])),
            reverse=True,
        )[:5]
        avg_top_duration = (
            sum(v.get("video_duration_seconds", 0) for v in top_videos) / len(top_videos) if top_videos else 0
        )
        all_video_duration = (
            sum(v.get("video_duration_seconds", 0) for v in video_posts) / len(video_posts) if video_posts else 0
        )

        if avg_top_duration > all_video_duration * 1.2:
            insights.append(
                {
                    "category": "recommendation",
                    "insight": f"Try posting longer videos — your top 5 videos are all >{int(avg_top_duration / 60)} minutes",
                    "metric": "video_duration_seconds",
                    "improvement_factor": round(avg_top_duration / max(all_video_duration, 1), 1),
                    "confidence": 0.6,
                    "sample_size": len(video_posts),
                }
            )

    insights.sort(key=lambda i: i["improvement_factor"], reverse=True)

    top_recommendations = [i["insight"] for i in insights[:5] if i["improvement_factor"] > 1.0]
    if not top_recommendations and insights:
        top_recommendations = [insights[0]["insight"]]

    return {
        "insights": insights,
        "top_recommendations": top_recommendations[:3],
        "generated_at": datetime.now(UTC).isoformat(),
    }
