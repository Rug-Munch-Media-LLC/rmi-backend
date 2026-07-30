"""Audience analytics — growth, churn, sources, protocol breakdown."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.core.logging import get_logger
from app.domains.analytics.services.helpers import (
    aggregate_to_monthly,
    aggregate_to_weekly,
    empty_audience_summary,
    empty_growth,
)

logger = get_logger(__name__)


def compute_audience_summary(
    followers: list[dict[str, Any]],
    views_by_follower: dict[str, list[dict[str, Any]]],
    engagements_by_follower: dict[str, list[dict[str, Any]]],
    period_days: int = 30,
) -> dict[str, Any]:
    """Compute audience analytics."""
    if not followers:
        return empty_audience_summary()

    total = len(followers)
    active = [f for f in followers if f.get("is_active", True)]
    inactive = [f for f in followers if not f.get("is_active", True)]

    # Protocol breakdown
    proto_breakdown: dict[str, int] = defaultdict(int)
    for f in followers:
        proto_breakdown[f.get("protocol", "unknown")] += 1

    # Engagement scoring
    scored: list[dict[str, Any]] = []
    for f in followers:
        fp = f.get("follower_fingerprint", "")
        f_views = views_by_follower.get(fp, [])
        f_engagements = engagements_by_follower.get(fp, [])
        score = min(100.0, (len(f_engagements) / max(len(f_views), 1)) * 50 + len(f_views) * 0.1)
        tips = sum(e.get("tip_amount_usd", 0) or 0 for e in f_engagements)
        scored.append(
            {
                "follower_handle": f.get("follower_handle", ""),
                "follower_wallet": f.get("follower_wallet", ""),
                "protocol": f.get("protocol", ""),
                "engagement_score": round(score, 1),
                "total_views": len(f_views),
                "total_tips_usd": round(tips, 2),
                "total_engagements": len(f_engagements),
                "followed_since": str(f.get("created_at", ""))[:10],
            }
        )

    scored.sort(key=lambda x: x["engagement_score"], reverse=True)

    # Churn
    churn_count = len(inactive)
    churn_rate = (churn_count / total * 100) if total else 0.0
    growth_rate = ((len(active) - churn_count) / max(total, 1)) * 100

    # Interest aggregation
    interest_counts: dict[str, int] = defaultdict(int)
    chain_counts: dict[str, int] = defaultdict(int)
    for f in followers:
        for interest in (f.get("interests", "") or "").split(","):
            interest = interest.strip()
            if interest:
                interest_counts[interest] += 1
        for chain in (f.get("chains_active", "") or "").split(","):
            chain = chain.strip()
            if chain:
                chain_counts[chain] += 1

    return {
        "total_followers": total,
        "followers_gained_30d": len(active) - churn_count if active else 0,
        "followers_lost_30d": churn_count,
        "net_growth_30d": len(active) - churn_count,
        "churn_rate_30d": round(churn_rate, 2),
        "growth_rate_30d": round(growth_rate, 2),
        "protocol_breakdown": dict(proto_breakdown),
        "top_interests": [
            {"interest": k, "count": v} for k, v in sorted(interest_counts.items(), key=lambda x: -x[1])[:10]
        ],
        "top_chains": [{"chain": k, "count": v} for k, v in sorted(chain_counts.items(), key=lambda x: -x[1])[:10]],
        "most_engaged_followers": scored[:20],
        "top_tippers": sorted(scored, key=lambda x: -x["total_tips_usd"])[:10],
        "growth_trend": [],
        "top_acquisition_sources": [],
    }


def compute_growth_analytics(
    followers: list[dict[str, Any]],
    period_days: int = 30,
) -> dict[str, Any]:
    """Compute audience growth trends."""
    if not followers:
        return empty_growth()

    # Daily growth
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"gained": 0, "lost": 0})
    for f in followers:
        day = str(f.get("created_at", ""))[:10]
        if f.get("is_active", True):
            daily[day]["gained"] += 1
        else:
            daily[day]["lost"] += 1

    daily_growth = [{"date": d, "value": v["gained"] - v["lost"], "label": d} for d, v in sorted(daily.items())]

    # Protocol growth
    proto_growth: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in followers:
        day = str(f.get("created_at", ""))[:10]
        proto = f.get("protocol", "unknown")
        if day:
            proto_growth[proto].append({"date": day, "value": 1, "label": day})

    proto_growth_agg = {}
    for proto, events in proto_growth.items():
        daily_counts: dict[str, int] = defaultdict(int)
        for e in events:
            daily_counts[e["date"]] += 1
        proto_growth_agg[proto] = [{"date": d, "value": c, "label": d} for d, c in sorted(daily_counts.items())]

    return {
        "daily_growth": daily_growth[-min(len(daily_growth), 30) :],
        "weekly_growth": aggregate_to_weekly(daily_growth),
        "monthly_growth": aggregate_to_monthly(daily_growth),
        "protocol_growth": proto_growth_agg,
        "acquisition_channels": [],
    }
