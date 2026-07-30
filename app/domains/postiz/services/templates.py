from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

def create_template(request_data: dict[str, Any]) -> dict[str, Any]:
    """Create a post template."""
    store = get_store()
    template = store.add_template(
        {
            "name": request_data["name"],
            "description": request_data.get("description", ""),
            "text": request_data["text"],
            "platforms": [p.lower() for p in request_data.get("platforms", [])],
            "media_ids": [],
            "tags": request_data.get("tags", []),
            "content_category": request_data.get("content_category", "general"),
            "variables": _extract_template_variables(request_data["text"]),
            "usage_count": 0,
        }
    )
    return template


def get_templates() -> dict[str, Any]:
    """Get all post templates."""
    store = get_store()
    templates = store.get_all_templates()
    categories = list(set(t.get("content_category", "general") for t in templates))
    categories.sort()

    return {
        "templates": templates,
        "total": len(templates),
        "categories": categories,
    }


def _extract_template_variables(text: str) -> list[str]:
    """Extract {{variable}} placeholders from template text."""
    import re

    return re.findall(r"\{\{(\w+)\}\}", text)


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


def _parse_period(period: str) -> int:
    """Convert period string (7d, 30d, 90d, 1y) to days."""
    period = period.lower().strip()
    if period.endswith("d"):
        return int(period[:-1])
    elif period.endswith("h"):
        return max(1, int(period[:-1]) // 24)
    elif period == "1y":
        return 365
    elif period == "all":
        return 3650
    else:
        try:
            return int(period)
        except ValueError:
            return 30


_metric_sim_counter: dict[str, int] = defaultdict(int)


def _simulate_metric(metric: str) -> int:
    """Simulate engagement metrics for demo purposes."""
    import random

    _metric_sim_counter[metric] += 1
    base = {
        "impressions": 500,
        "engagements": 45,
        "likes": 25,
        "comments": 8,
        "shares": 12,
        "clicks": 15,
        "followers_gained": 3,
    }
    b = base.get(metric, 10)
    return max(1, int(b * (0.3 + random.random() * 1.4)))


def _generate_trend(days: int) -> list[dict[str, Any]]:
    """Generate a simulated trend line."""
    import random

    trend = []
    base = 100
    for i in range(min(days, 90)):
        day = (datetime.now(UTC) - timedelta(days=days - i)).strftime("%Y-%m-%d")
        base += random.randint(-20, 30)
        trend.append({"date": day, "value": max(0, base), "label": day})
    return trend

