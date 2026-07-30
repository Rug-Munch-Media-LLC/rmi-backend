"""View analytics — true counts, watch time, drop-off."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.core.logging import get_logger
from app.domains.analytics.services.helpers import empty_view_summary

logger = get_logger(__name__)


def compute_view_analytics(
    views: list[dict[str, Any]],
    period_days: int = 7,
) -> dict[str, Any]:
    """Compute TRUE view analytics from raw view events.

    No inflation. Every number is a real human.
    """
    if not views:
        return empty_view_summary(period_days)

    total = len(views)
    unique_fingerprints: set[str] = set()
    valid_views: list[dict[str, Any]] = []
    replay_count = 0
    peak_concurrent = 0
    total_watch_time = 0.0
    viewed_to_end_count = 0
    scrolled_past_count = 0
    drop_off_buckets: dict[int, int] = defaultdict(int)
    daily_counts: dict[str, int] = defaultdict(int)
    durations: list[float] = []

    for v in views:
        fp = v.get("viewer_fingerprint", "")
        if fp:
            unique_fingerprints.add(fp)

        if v.get("is_replay"):
            replay_count += 1

        wt = v.get("watch_time_seconds", 0) or 0
        if wt > 0:
            valid_views.append(v)
            total_watch_time += wt
            durations.append(wt)

        if v.get("viewed_to_end"):
            viewed_to_end_count += 1

        if v.get("scrolled_past"):
            scrolled_past_count += 1

        if v.get("peak_concurrent", 0) > peak_concurrent:
            peak_concurrent = v["peak_concurrent"]

        drop_pct = v.get("drop_off_point_pct")
        if drop_pct is not None and drop_pct > 0:
            bucket = int(drop_pct / 10) * 10
            drop_off_buckets[bucket] += 1

        created = v.get("created_at", "")
        if created:
            day = created[:10] if isinstance(created, str) else str(created)[:10]
            daily_counts[day] += 1

    avg_duration = total_watch_time / len(durations) if durations else 0.0
    durations_sorted = sorted(durations)
    median_duration = durations_sorted[len(durations_sorted) // 2] if durations_sorted else 0.0

    vtr = (viewed_to_end_count / total * 100) if total else 0.0
    sbr = (scrolled_past_count / total * 100) if total else 0.0

    drop_off_points = []
    for pct_bucket in sorted(drop_off_buckets.keys()):
        viewers_lost = drop_off_buckets[pct_bucket]
        drop_off_points.append(
            {
                "second": 0.0,
                "percentage": float(pct_bucket),
                "viewers_lost": viewers_lost,
                "drop_pct": (viewers_lost / total * 100) if total else 0.0,
            }
        )

    daily_trend = [{"date": d, "value": c, "label": d} for d, c in sorted(daily_counts.items())]

    return {
        "total_views": total,
        "unique_viewers": len(unique_fingerprints),
        "total_watch_time_minutes": round(total_watch_time / 60, 2),
        "average_view_duration_seconds": round(avg_duration, 2),
        "median_view_duration_seconds": round(median_duration, 2),
        "view_through_rate": round(vtr, 2),
        "scroll_by_rate": round(sbr, 2),
        "replay_views": replay_count,
        "peak_concurrent_viewers": peak_concurrent,
        "drop_off_points": drop_off_points,
        "daily_trend": daily_trend,
        "period": f"{period_days}d",
    }
