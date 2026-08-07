from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.domains.postiz.platforms import PLATFORMS, get_platform
from app.domains.postiz.services.store import get_store

logger = get_logger(__name__)

# Auto-queue interval (restored from original service.py — lost during the
# service package split).
OPTIMAL_INTERVAL_MINUTES = 240  # 4 hours between auto-queue posts


def schedule_post(request_data: dict[str, Any]) -> dict[str, Any]:
    """Schedule a post for publishing."""
    store = get_store()
    platform_ids = [p.lower() for p in request_data.get("platforms", [])]

    # Verify platforms exist
    for pid in platform_ids:
        if not get_platform(pid):
            raise ValueError(f"Unknown platform: {pid}")

    post = store.add_post(
        {
            "text": request_data["text"],
            "platforms": platform_ids,
            "platform_names": [PLATFORMS[p].name for p in platform_ids if p in PLATFORMS],
            "media_urls": request_data.get("media_urls", []),
            "tags": request_data.get("tags", []),
            "content_category": request_data.get("content_category", "general"),
            "scheduled_at": request_data.get("scheduled_at"),
            "timezone": request_data.get("timezone", "UTC"),
            "thread": request_data.get("thread", []),
            "cross_post_to": request_data.get("cross_post_to", []),
            "queue_position": request_data.get("queue_position"),
        }
    )

    # Track media usage
    media_ids = request_data.get("media_ids", [])
    for mid in media_ids:
        store.increment_media_usage(mid)

    return post


def bulk_schedule(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Bulk schedule posts from entries."""
    store = get_store()
    results = []
    errors = []

    for i, entry in enumerate(entries):
        try:
            for pid in entry.get("platforms", []):
                if not get_platform(pid.lower()):
                    raise ValueError(f"Entry {i}: unknown platform '{pid}'")

            post = store.add_post(
                {
                    "text": entry["text"],
                    "platforms": [p.lower() for p in entry.get("platforms", [])],
                    "platform_names": [
                        PLATFORMS[p.lower()].name for p in entry.get("platforms", []) if p.lower() in PLATFORMS
                    ],
                    "media_urls": entry.get("media_urls", []),
                    "tags": entry.get("tags", []),
                    "content_category": entry.get("content_category", "general"),
                    "scheduled_at": entry.get("scheduled_at"),
                    "timezone": entry.get("timezone", "UTC"),
                }
            )
            results.append(post)
        except Exception as exc:
            errors.append({"index": i, "error": str(exc)})

    return {
        "total": len(entries),
        "scheduled": len(results),
        "failed": len(errors),
        "errors": errors,
        "posts": results,
    }


def get_calendar(start_date: str, end_date: str) -> dict[str, Any]:
    """Get the content calendar for a date range."""
    store = get_store()
    posts = store.get_posts_in_range(start_date, end_date)

    # Group by date
    days_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in posts:
        day = (p.get("scheduled_at") or p.get("created_at", ""))[:10]
        days_map[day].append(p)

    # Build day-by-day calendar
    start = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()
    days = []
    gaps = []
    total_scheduled = 0
    total_published = 0
    total_drafts = 0
    platform_breakdown: dict[str, int] = defaultdict(int)
    gap_streak = 0

    current = start
    while current <= end:
        date_str = current.isoformat()
        day_posts = days_map.get(date_str, [])
        count = len(day_posts)

        for p in day_posts:
            s = p.get("status", "")
            if s == "published":
                total_published += 1
            elif s == "scheduled":
                total_scheduled += 1
            elif s == "draft":
                total_drafts += 1
            for pid in p.get("platforms", []):
                platform_breakdown[pid] += 1

        has_gap = count == 0
        if has_gap:
            gap_streak += 1
        else:
            gap_streak = 0

        day_entry = {
            "date": date_str,
            "posts": day_posts,
            "post_count": count,
            "has_gap": has_gap,
            "gap_days": gap_streak,
        }
        days.append(day_entry)

        if has_gap:
            gaps.append(day_entry)

        current += timedelta(days=1)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": days,
        "total_scheduled": total_scheduled,
        "total_published": total_published,
        "total_drafts": total_drafts,
        "gaps": gaps,
        "platform_breakdown": dict(platform_breakdown),
    }


def get_queue() -> dict[str, Any]:
    """Get the posting queue."""
    store = get_store()
    queue = store.get_queue()

    platform_queue: dict[str, int] = defaultdict(int)
    for p in queue:
        for pid in p.get("platforms", []):
            platform_queue[pid] += 1

    fill_days = len(queue) * (OPTIMAL_INTERVAL_MINUTES / 60 / 24) if queue else 0

    return {
        "posts": queue,
        "total": len(queue),
        "estimated_fill_days": round(fill_days, 1),
        "optimal_interval_minutes": OPTIMAL_INTERVAL_MINUTES,
        "platform_queue": dict(platform_queue),
    }


def create_recurring(request_data: dict[str, Any]) -> dict[str, Any]:
    """Create a recurring post schedule."""
    store = get_store()
    pattern = request_data.get("pattern", "weekly")

    # Calculate next post date
    now = datetime.now(UTC)
    if pattern == "daily":
        next_post = (now + timedelta(days=1)).isoformat()
    elif pattern == "weekly":
        next_post = (now + timedelta(days=7)).isoformat()
    elif pattern == "monthly":
        next_post = (now + timedelta(days=30)).isoformat()
    else:
        next_post = (now + timedelta(days=7)).isoformat()

    recurring = store.add_recurring(
        {
            "text": request_data["text"],
            "platforms": [p.lower() for p in request_data.get("platforms", [])],
            "platform_names": [
                PLATFORMS[p.lower()].name for p in request_data.get("platforms", []) if p.lower() in PLATFORMS
            ],
            "pattern": pattern,
            "cron_expression": request_data.get("recurring_cron", ""),
            "media_urls": request_data.get("media_urls", []),
            "tags": request_data.get("tags", []),
            "content_category": request_data.get("content_category", "general"),
            "start_date": now.isoformat(),
            "end_date": request_data.get("recurring_end"),
            "last_posted": None,
            "next_post": next_post,
            "times_posted": 0,
        }
    )

    return recurring
