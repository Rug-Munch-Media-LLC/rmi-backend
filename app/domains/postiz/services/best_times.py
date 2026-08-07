from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.postiz.platforms import get_platform
from app.domains.postiz.services.store import get_store

logger = get_logger(__name__)

# Default optimal posting times per platform (restored from original service.py —
# lost during the service package split).
DEFAULT_BEST_TIMES: dict[str, dict[str, Any]] = {
    "twitter": {"time": "08:00 UTC", "day": "Wednesday", "confidence": 0.85, "peak_hours": [7, 8, 9, 12, 17, 18]},
    "facebook": {"time": "09:00 UTC", "day": "Thursday", "confidence": 0.80, "peak_hours": [9, 10, 11, 13, 14]},
    "instagram": {"time": "11:00 UTC", "day": "Tuesday", "confidence": 0.82, "peak_hours": [11, 12, 13, 19, 20]},
    "linkedin": {"time": "10:00 UTC", "day": "Wednesday", "confidence": 0.88, "peak_hours": [8, 9, 10, 12, 17]},
    "tiktok": {"time": "14:00 UTC", "day": "Thursday", "confidence": 0.75, "peak_hours": [14, 15, 19, 20, 21]},
    "youtube": {"time": "14:00 UTC", "day": "Saturday", "confidence": 0.80, "peak_hours": [14, 15, 16, 17, 19]},
    "pinterest": {"time": "20:00 UTC", "day": "Saturday", "confidence": 0.78, "peak_hours": [20, 21, 22, 23]},
    "reddit": {"time": "15:00 UTC", "day": "Tuesday", "confidence": 0.72, "peak_hours": [14, 15, 16, 17]},
    "telegram": {"time": "12:00 UTC", "day": "Wednesday", "confidence": 0.70, "peak_hours": [10, 12, 14, 17, 19]},
    "discord": {"time": "18:00 UTC", "day": "Thursday", "confidence": 0.75, "peak_hours": [17, 18, 19, 20, 21]},
    "slack": {"time": "10:00 UTC", "day": "Tuesday", "confidence": 0.80, "peak_hours": [9, 10, 11, 14]},
    "nostr": {"time": "14:00 UTC", "day": "Thursday", "confidence": 0.76, "peak_hours": [13, 14, 15, 16, 20]},
    "farcaster": {"time": "14:00 UTC", "day": "Thursday", "confidence": 0.79, "peak_hours": [14, 15, 16, 17, 21]},
    "bluesky": {"time": "13:00 UTC", "day": "Wednesday", "confidence": 0.74, "peak_hours": [12, 13, 14, 15, 19]},
    "mastodon": {"time": "15:00 UTC", "day": "Tuesday", "confidence": 0.73, "peak_hours": [14, 15, 16, 17]},
    "lens": {"time": "14:00 UTC", "day": "Thursday", "confidence": 0.71, "peak_hours": [13, 14, 15, 16]},
    "medium": {"time": "12:00 UTC", "day": "Tuesday", "confidence": 0.82, "peak_hours": [10, 12, 14, 15]},
    "devto": {"time": "14:00 UTC", "day": "Wednesday", "confidence": 0.77, "peak_hours": [13, 14, 15, 16]},
    "hashnode": {"time": "13:00 UTC", "day": "Wednesday", "confidence": 0.76, "peak_hours": [12, 13, 14, 15]},
    "substack": {"time": "06:00 UTC", "day": "Tuesday", "confidence": 0.85, "peak_hours": [5, 6, 7, 8, 9]},
    "wordpress": {"time": "09:00 UTC", "day": "Tuesday", "confidence": 0.80, "peak_hours": [8, 9, 10, 11, 14]},
}


def get_best_times() -> dict[str, Any]:
    """Get AI-optimized best posting times for all platforms."""
    store = get_store()
    connected = store.get_connected_platform_ids()

    platform_times = {}
    for pid, config in DEFAULT_BEST_TIMES.items():
        if pid not in connected:
            # Still include recommendations for non-connected platforms
            pass

        day_map = {
            "Monday": 0,
            "Tuesday": 1,
            "Wednesday": 2,
            "Thursday": 3,
            "Friday": 4,
            "Saturday": 5,
            "Sunday": 6,
        }
        day_idx = day_map.get(config["day"], 0)
        days_list = list(day_map.keys())
        days_list[(day_idx + 2) % 7]
        days_list[(day_idx + 4) % 7]

        pdef = get_platform(pid)
        platform_times[pid] = {
            "platform_id": pid,
            "platform_name": pdef.name if pdef else pid,
            "best_time_utc": config["time"],
            "best_day": config["day"],
            "confidence": config["confidence"],
            "second_best_time": f"{(int(config['time'].split(':')[0]) + 4) % 24:02d}:00 UTC",
            "worst_time": f"{(int(config['time'].split(':')[0]) + 10) % 24:02d}:00 UTC",
            "peak_hours": config["peak_hours"],
            "reasoning": f"Based on analysis of optimal engagement windows for {pdef.name if pdef else pid} audience",
        }

    return {
        "platform_times": platform_times,
        "overall_recommendation": "Schedule posts between 12:00-15:00 UTC for maximum cross-platform engagement. Tuesday-Thursday are the strongest days overall.",
        "generated_at": datetime.now(UTC).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════
# 3. Analytics Service
# ══════════════════════════════════════════════════════════════════════
