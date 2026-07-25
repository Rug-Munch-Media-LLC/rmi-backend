"""Creator Analytics — Pydantic request/response models.

Structured data types for all five analytics pillars:
  1. True view counts
  2. Engagement analytics
  3. Cross-protocol analytics
  4. Content improvement insights
  5. Audience analytics
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── View Analytics ─────────────────────────────────────────────────────


class ViewAnalyticsSummary(BaseModel):
    """True view analytics for a creator or post."""

    total_views: int = Field(0, description="Total view events (deduplicated)")
    unique_viewers: int = Field(0, description="Unique viewers by fingerprint/wallet")
    total_watch_time_minutes: float = Field(0.0, description="Total minutes watched")
    average_view_duration_seconds: float = Field(0.0, description="Average view duration")
    median_view_duration_seconds: float = Field(0.0, description="Median view duration")
    view_through_rate: float = Field(0.0, description="% who watched to end (0-100)")
    scroll_by_rate: float = Field(0.0, description="% who scrolled past without watching (0-100)")
    replay_views: int = Field(0, description="Replay views of live/VOD content")
    peak_concurrent_viewers: int = Field(0, description="Peak concurrent (live)")
    drop_off_points: list[DropOffPoint] = Field(default_factory=list)
    daily_trend: list[DataPoint] = Field(default_factory=list)
    period: str = "7d"


class DropOffPoint(BaseModel):
    """Where viewers stopped watching."""

    second: float = Field(0.0, description="Timestamp in seconds")
    percentage: float = Field(0.0, description="% through content")
    viewers_lost: int = Field(0, description="Viewers who dropped at this point")
    drop_pct: float = Field(0.0, description="% of total viewers who dropped here")


class DataPoint(BaseModel):
    """A single time-series data point."""

    date: str = Field("", description="ISO date string")
    value: float = Field(0.0, description="Metric value")
    label: str = ""


class ViewAnalyticsDetail(BaseModel):
    """Detailed per-post view analytics."""

    post_id: str = ""
    post_title: str = ""
    content_type: str = ""
    published_at: str = ""
    summary: ViewAnalyticsSummary = Field(default_factory=ViewAnalyticsSummary)
    protocol_breakdown: dict[str, ViewAnalyticsSummary] = Field(
        default_factory=dict,
        description="Breakdown by protocol (nostr, farcaster, bluesky, mastodon)",
    )


# ── Engagement Analytics ───────────────────────────────────────────────


class EngagementAnalyticsSummary(BaseModel):
    """Engagement metrics for a creator or post."""

    total_engagements: int = Field(0, description="Total engagement actions")
    unique_engagers: int = Field(0, description="Unique people who engaged")

    # Likes
    likes: int = Field(0, description="Unique likes")
    unique_likers: int = Field(0, description="Unique people who liked")

    # Comments
    comments: int = Field(0, description="Total comments")
    unique_commenters: int = Field(0, description="Unique people who commented")
    comment_sentiment_positive: int = Field(0, description="Positive comments")
    comment_sentiment_neutral: int = Field(0, description="Neutral comments")
    comment_sentiment_negative: int = Field(0, description="Negative comments")
    average_sentiment_score: float = Field(0.0, description="-1.0 to 1.0")

    # Reposts / Shares
    reposts: int = Field(0, description="Total reposts/shares across protocols")

    # Tips
    total_tips_usd: float = Field(0.0, description="Total tips in USD")
    total_tips_token: float = Field(0.0, description="Total tips in token amounts")
    average_tip_usd: float = Field(0.0, description="Average tip amount in USD")
    tip_count: int = Field(0, description="Number of tips")
    tip_velocity: float = Field(0.0, description="Tips per day")
    top_tip_token: str = Field("", description="Most used tip token")

    # Follows
    followers_gained: int = Field(0, description="Followers gained from this content")

    # Click-through
    click_through_rate: float = Field(0.0, description="CTR as % (0-100)")
    total_clicks: int = Field(0, description="Total link clicks")

    # Saves / Bookmarks
    saves: int = Field(0, description="Save/bookmark count")
    save_rate: float = Field(0.0, description="Save rate as % of viewers")

    # Read time (articles)
    average_read_time_seconds: float = Field(0.0, description="Average time spent reading")
    read_completion_rate: float = Field(0.0, description="% who finished reading")

    period: str = "7d"


class EngagementAnalyticsDetail(BaseModel):
    """Detailed per-post engagement analytics."""

    post_id: str = ""
    post_title: str = ""
    content_type: str = ""
    summary: EngagementAnalyticsSummary = Field(default_factory=EngagementAnalyticsSummary)
    protocol_breakdown: dict[str, EngagementAnalyticsSummary] = Field(default_factory=dict)
    top_comments: list[dict[str, Any]] = Field(default_factory=list)


# ── Cross-Protocol Analytics ───────────────────────────────────────────


class ProtocolComparison(BaseModel):
    """Performance comparison for a single protocol."""

    protocol: str = ""
    views: int = 0
    unique_viewers: int = 0
    total_watch_time_minutes: float = 0.0
    average_view_duration_seconds: float = 0.0
    engagements: int = 0
    likes: int = 0
    comments: int = 0
    reposts: int = 0
    tips_usd: float = 0.0
    followers_gained: int = 0
    engagement_rate: float = 0.0  # engagements per view as %
    best_posting_time: str = ""  # e.g. "14:00 UTC"
    best_content_type: str = ""  # e.g. "video"
    reach_score: float = 0.0  # normalized 0-100
    engagement_score: float = 0.0  # normalized 0-100


class CrossProtocolSummary(BaseModel):
    """Cross-protocol analytics comparing all protocols."""

    comparisons: list[ProtocolComparison] = Field(default_factory=list)
    best_protocol_for_reach: str = ""
    best_protocol_for_engagement: str = ""
    best_protocol_for_tips: str = ""
    total_cross_protocol_views: int = 0
    total_unique_viewers: int = 0
    period: str = "7d"


# ── Content Improvement Insights ──────────────────────────────────────


class ContentInsight(BaseModel):
    """A single data-driven insight about content performance."""

    category: str = Field(
        "", description="media_type, video_length, posting_time, content_category, protocol_timing, recommendation"
    )
    insight: str = Field(
        "",
        description='Human-readable insight like "Your posts with images get 2.3x more engagement"',
    )
    metric: str = Field("", description="The measured metric")
    improvement_factor: float = Field(0.0, description="Multiplier vs baseline (e.g. 2.3 means 2.3x)")
    confidence: float = Field(0.0, description="Statistical confidence 0-1")
    sample_size: int = Field(0, description="Number of data points")


class ContentInsightsResponse(BaseModel):
    """AI-driven content improvement insights."""

    insights: list[ContentInsight] = Field(default_factory=list)
    top_recommendations: list[str] = Field(default_factory=list)
    generated_at: str = ""


# ── Audience Analytics ─────────────────────────────────────────────────


class AudienceSummary(BaseModel):
    """Audience analytics overview."""

    total_followers: int = 0
    followers_gained_30d: int = 0
    followers_lost_30d: int = 0
    net_growth_30d: int = 0
    churn_rate_30d: float = 0.0  # %
    growth_rate_30d: float = 0.0  # %

    # Demographics
    protocol_breakdown: dict[str, int] = Field(
        default_factory=dict,
        description="Followers per protocol",
    )
    top_interests: list[dict[str, Any]] = Field(default_factory=list)
    top_chains: list[dict[str, Any]] = Field(default_factory=list)

    # Top followers
    most_engaged_followers: list[FollowerSummary] = Field(default_factory=list)
    top_tippers: list[FollowerSummary] = Field(default_factory=list)

    # Growth trend
    growth_trend: list[DataPoint] = Field(default_factory=list)

    # Source attribution
    top_acquisition_sources: list[dict[str, Any]] = Field(default_factory=list)


class FollowerSummary(BaseModel):
    """Summary of a single follower's engagement."""

    follower_handle: str = ""
    follower_wallet: str = ""
    protocol: str = ""
    engagement_score: float = 0.0
    total_views: int = 0
    total_tips_usd: float = 0.0
    total_engagements: int = 0
    followed_since: str = ""


class GrowthAnalytics(BaseModel):
    """Audience growth over time."""

    daily_growth: list[DataPoint] = Field(default_factory=list)
    weekly_growth: list[DataPoint] = Field(default_factory=list)
    monthly_growth: list[DataPoint] = Field(default_factory=list)
    protocol_growth: dict[str, list[DataPoint]] = Field(default_factory=dict)
    acquisition_channels: list[dict[str, Any]] = Field(default_factory=list)
