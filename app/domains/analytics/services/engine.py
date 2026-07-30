"""CreatorAnalyticsEngine — facade that delegates to module-level analytics functions."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.analytics.services.audience import (
    compute_audience_summary,
    compute_growth_analytics,
)
from app.domains.analytics.services.buffer import AnalyticsBuffer
from app.domains.analytics.services.content import compute_content_insights
from app.domains.analytics.services.engagement import (
    compute_cross_protocol_analytics,
    compute_engagement_analytics,
)
from app.domains.analytics.services.view_analytics import compute_view_analytics

logger = get_logger(__name__)


class CreatorAnalyticsEngine:
    """Core analytics engine for creator metrics.

    Facade that delegates computation to module-level functions.
    Maintains the AnalyticsBuffer for event ingestion.
    """

    def __init__(self):
        self._buffer = AnalyticsBuffer()
        self._insight_cache: dict[str, list[dict[str, Any]]] = {}
        self._insight_cache_ts: float = 0

    # ── Event Ingestion ────────────────────────────────────────────

    async def record_view(self, *args, **kwargs) -> bool:
        """Record a view event. Delegates to buffer."""
        from datetime import UTC, datetime

        self._buffer.add_view(
            {
                "post_id": kwargs.get("post_id", ""),
                "creator_id": kwargs.get("creator_id", ""),
                "viewer_fingerprint": kwargs.get("viewer_fingerprint", ""),
                "viewer_wallet": kwargs.get("viewer_wallet", ""),
                "viewer_ip_hash": kwargs.get("viewer_ip_hash", "") or (kwargs.get("viewer_fingerprint", "")[:16]),
                "session_id": kwargs.get("session_id", ""),
                "protocol": kwargs.get("protocol", "nostr"),
                "watch_time_seconds": kwargs.get("watch_time_seconds", 0.0),
                "total_content_duration": kwargs.get("total_duration", 0.0),
                "dropped_at_seconds": kwargs.get("dropped_at"),
                "drop_off_point_pct": (
                    kwargs.get("dropped_at") / kwargs.get("total_duration", 1) * 100
                    if kwargs.get("dropped_at") and kwargs.get("total_duration", 0) > 0
                    else None
                ),
                "viewed_to_end": kwargs.get("dropped_at") is None
                or (
                    kwargs.get("total_duration", 0) > 0
                    and kwargs.get("dropped_at", 0) >= kwargs.get("total_duration", 1) * 0.95
                ),
                "scrolled_past": kwargs.get("scrolled_past", False),
                "scroll_depth_pct": kwargs.get("scroll_depth", 0.0),
                "is_live_view": kwargs.get("is_live", False),
                "is_replay": kwargs.get("is_replay", False),
                "referrer": kwargs.get("referrer", ""),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        return True

    async def record_engagement(self, *args, **kwargs) -> None:
        """Record an engagement event. Delegates to buffer."""
        from datetime import UTC, datetime

        self._buffer.add_engagement(
            {
                "post_id": kwargs.get("post_id", ""),
                "creator_id": kwargs.get("creator_id", ""),
                "viewer_fingerprint": kwargs.get("viewer_fingerprint", ""),
                "engagement_type": kwargs.get("engagement_type", ""),
                "protocol": kwargs.get("protocol", "nostr"),
                "tip_amount_usd": kwargs.get("tip_amount_usd", 0.0),
                "tip_token": kwargs.get("tip_token", ""),
                "tip_amount_token": kwargs.get("tip_amount_token", 0.0),
                "comment_text": kwargs.get("comment_text", ""),
                "comment_sentiment": kwargs.get("comment_sentiment", "neutral"),
                "comment_sentiment_score": kwargs.get("sentiment_score", 0.0),
                "clicked_url": kwargs.get("clicked_url", ""),
                "followed_after": kwargs.get("followed_after", False),
                "external_engagement_id": kwargs.get("external_id", ""),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    async def record_follow(self, *args, **kwargs) -> None:
        """Record a new follower. Delegates to buffer."""
        from datetime import UTC, datetime

        self._buffer.add_follower(
            {
                "creator_id": kwargs.get("creator_id", ""),
                "follower_fingerprint": kwargs.get("follower_fingerprint", ""),
                "follower_wallet": kwargs.get("follower_wallet", ""),
                "follower_handle": kwargs.get("follower_handle", ""),
                "protocol": kwargs.get("protocol", "nostr"),
                "source_post_id": kwargs.get("source_post_id"),
                "is_active": True,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    # ── Analytics Computation (delegates to module functions) ──────

    def compute_view_analytics(
        self,
        views: list[dict[str, Any]],
        period_days: int = 7,
    ) -> dict[str, Any]:
        """Compute TRUE view analytics from raw view events."""
        return compute_view_analytics(views, period_days)

    def compute_engagement_analytics(
        self,
        engagements: list[dict[str, Any]],
        period_days: int = 7,
    ) -> dict[str, Any]:
        """Compute engagement analytics from raw events."""
        return compute_engagement_analytics(engagements, period_days)

    def compute_cross_protocol_analytics(
        self,
        views_by_protocol: dict[str, list[dict[str, Any]]],
        engagements_by_protocol: dict[str, list[dict[str, Any]]],
        period_days: int = 7,
    ) -> dict[str, Any]:
        """Compare performance across all protocols."""
        return compute_cross_protocol_analytics(views_by_protocol, engagements_by_protocol, period_days)

    def compute_content_insights(
        self,
        posts: list[dict[str, Any]],
        views_by_post: dict[str, list[dict[str, Any]]],
        engagements_by_post: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Generate data-driven content improvement insights."""
        return compute_content_insights(posts, views_by_post, engagements_by_post)

    def compute_audience_summary(
        self,
        followers: list[dict[str, Any]],
        views_by_follower: dict[str, list[dict[str, Any]]],
        engagements_by_follower: dict[str, list[dict[str, Any]]],
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Compute audience analytics."""
        return compute_audience_summary(followers, views_by_follower, engagements_by_follower, period_days)

    def compute_growth_analytics(
        self,
        followers: list[dict[str, Any]],
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Compute audience growth trends."""
        return compute_growth_analytics(followers, period_days)


# ── Singleton ──────────────────────────────────────────────────────────

_engine_instance: CreatorAnalyticsEngine | None = None


def get_analytics_engine() -> CreatorAnalyticsEngine:
    """Get or create the singleton analytics engine."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = CreatorAnalyticsEngine()
    return _engine_instance
