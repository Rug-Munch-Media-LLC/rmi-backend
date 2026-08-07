from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.postiz.platforms import get_platform
from app.domains.postiz.services.store import get_store

logger = get_logger(__name__)

# Gap-analysis thresholds (restored from original service.py — lost during the
# service package split).
GAP_WARNING_DAYS = 2
GAP_CRITICAL_DAYS = 5


def get_gap_analysis() -> dict[str, Any]:
    """Analyze posting gaps across all connected platforms."""
    store = get_store()
    connected = store.get_connected_platform_ids()
    now = datetime.now(UTC)

    gaps = []
    platform_summary_dict = {}
    critical_count = 0
    warning_count = 0

    for pid in connected:
        pdef = get_platform(pid)
        if not pdef:
            continue

        last_date_str = store.get_last_post_date(pid)
        days_since = 0
        if last_date_str:
            try:
                last_date = datetime.fromisoformat(last_date_str)
                days_since = (now - last_date).days
            except (ValueError, TypeError):
                days_since = 999

        posts_week = store.get_posts_this_period(pid, 7)
        posts_month = store.get_posts_this_period(pid, 30)

        if days_since >= GAP_CRITICAL_DAYS or posts_month == 0:
            status = "critical"
            critical_count += 1
        elif days_since >= GAP_WARNING_DAYS:
            status = "warning"
            warning_count += 1
        else:
            status = "ok"

        gap = {
            "platform_id": pid,
            "platform_name": pdef.name,
            "days_since_last_post": days_since,
            "status": status,
            "recommended_action": _get_gap_action(pid, days_since, status),
        }
        gaps.append(gap)

        platform_summary_dict[pid] = {
            "last_post": last_date_str,
            "days_since_last": days_since,
            "posts_this_week": posts_week,
            "posts_this_month": posts_month,
            "avg_posts_per_week": round(posts_month / 4.33, 1) if posts_month else 0.0,
            "gap_status": status,
        }

    if critical_count > 0:
        overall = "critical"
    elif warning_count > 0:
        overall = "warning"
    else:
        overall = "good"

    recommendations = _generate_gap_recommendations(gaps)

    return {
        "gaps": gaps,
        "platform_summary": platform_summary_dict,
        "overall_health": overall,
        "recommendations": recommendations,
    }


def _get_gap_action(platform_id: str, days_since: int, status: str) -> str:
    pdef = get_platform(platform_id)
    name = pdef.name if pdef else platform_id

    if status == "critical":
        return f"⚠️ You haven't posted on {name} in {days_since} days. Schedule a post now to re-engage your audience."
    elif status == "warning":
        return f"📅 It's been {days_since} days since your last {name} post. Consider scheduling one soon."
    return f"✅ {name} posting schedule looks healthy."


def _generate_gap_recommendations(gaps: list[dict[str, Any]]) -> list[str]:
    recs = []
    critical = [g for g in gaps if g["status"] == "critical"]
    warning = [g for g in gaps if g["status"] == "warning"]

    if critical:
        platforms_str = ", ".join(g["platform_name"] for g in critical[:3])
        recs.append(f"🚨 Prioritize posting on {platforms_str} — critical gaps detected")
    if warning:
        platforms_str = ", ".join(g["platform_name"] for g in warning[:3])
        recs.append(f"📅 Schedule posts for {platforms_str} this week to prevent gaps")

    if not critical and not warning:
        recs.append("✅ All platforms are active — great content consistency!")

    # Cross-platform recommendation
    if len(gaps) >= 3:
        recs.append("💡 Try cross-posting content across multiple platforms to maximize reach")

    return recs
