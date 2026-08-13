"""Postiz Premium — Pydantic request/response models.

Full model definitions for:
  1. Platform management (connect, list, auth)
  2. Content scheduling (calendar, queue, recurring, bulk)
  3. Analytics (cross-platform, per-platform, export)
  4. Content studio (compose, preview, media, templates)
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ══════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════


class PostStatus(StrEnum):
    draft = "draft"
    scheduled = "scheduled"
    queued = "queued"
    publishing = "publishing"
    published = "published"
    failed = "failed"
    cancelled = "cancelled"


class RecurrencePattern(StrEnum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    custom_cron = "custom_cron"


class ContentCategoryEnum(StrEnum):
    educational = "educational"
    promotional = "promotional"
    engagement = "engagement"
    news = "news"
    entertainment = "entertainment"
    thought_leadership = "thought_leadership"
    tutorial = "tutorial"
    announcement = "announcement"
    case_study = "case_study"
    user_generated = "user_generated"
    curated = "curated"
    behind_the_scenes = "behind_the_scenes"


# ══════════════════════════════════════════════════════════════════════
# 1. Platform Management
# ══════════════════════════════════════════════════════════════════════


class PlatformConnectRequest(BaseModel):
    """Request to connect a platform account."""

    platform_id: str = Field(..., min_length=1, max_length=50, description="Platform slug (e.g. 'twitter', 'nostr')")
    oauth_code: str | None = Field(default=None, description="OAuth authorization code")
    api_key: str | None = Field(default=None, description="API key (for platforms without OAuth)")
    api_secret: str | None = Field(default=None, description="API secret")
    access_token: str | None = Field(default=None, description="Pre-existing access token")
    instance_url: str | None = Field(default=None, description="Instance URL (for federated platforms like Mastodon)")
    webhook_url: str | None = Field(default=None, description="Webhook URL (for Discord, Slack)")
    label: str | None = Field(default=None, max_length=100, description="Human label for this connection")


class PlatformConnection(BaseModel):
    """A connected platform account."""

    id: str = Field(..., description="Connection UUID")
    platform_id: str = Field(..., description="Platform slug")
    platform_name: str = Field(..., description="Platform display name")
    account_name: str = Field(..., description="Connected account name/handle")
    account_id: str = Field(default="", description="Platform-specific account ID")
    avatar_url: str = Field(default="", description="Account avatar")
    connected_at: str = Field(..., description="ISO timestamp")
    expires_at: str | None = Field(default=None, description="Token expiry")
    label: str = Field(default="", description="User-defined label")
    status: str = Field(default="active", description="active, expired, revoked")
    rate_limit_remaining: int = Field(default=0, description="API calls remaining this window")
    rate_limit_reset: str = Field(default="", description="Rate limit reset timestamp")


class PlatformListResponse(BaseModel):
    """List of all available platforms with connection status."""

    platforms: list[PlatformInfo] = Field(default_factory=list)
    total: int = 0
    connected_count: int = 0
    decentralized_count: int = 0
    categories: dict[str, int] = Field(default_factory=dict)


class PlatformInfo(BaseModel):
    """Public platform info with optional connection status."""

    id: str
    name: str
    category: str
    max_length: int
    media_types: list[str]
    decentralized: bool = False
    icon: str = "🌐"
    status: str = "active"
    connected: bool = False
    connection_id: str | None = None
    account_name: str | None = None


class ConnectResponse(BaseModel):
    """Response after connecting a platform."""

    success: bool = True
    connection: PlatformConnection
    message: str = "Platform connected successfully"
    onboarding_url: str | None = None


# ══════════════════════════════════════════════════════════════════════
# 2. Content Scheduling
# ══════════════════════════════════════════════════════════════════════


class SchedulePostRequest(BaseModel):
    """Request to schedule a post."""

    text: str = Field(..., min_length=1, max_length=64000, description="Post content")
    platforms: list[str] = Field(..., min_length=1, max_length=30, description="Target platform IDs")
    media_urls: list[str] = Field(default_factory=list, max_length=20, description="Media URLs")
    tags: list[str] = Field(default_factory=list, max_length=30, description="Hashtags/tags")
    content_category: str = Field(default="general", description="Content category for rotation")
    scheduled_at: str | None = Field(default=None, description="ISO timestamp for scheduled posting")
    timezone: str = Field(default="UTC", description="Timezone for scheduled time")
    recurring: RecurrencePattern | None = Field(default=None, description="Recurrence pattern")
    recurring_cron: str | None = Field(default=None, description="Custom cron expression")
    recurring_end: str | None = Field(default=None, description="Recurrence end date ISO")
    queue_position: int | None = Field(default=None, ge=1, description="Position in queue (if not scheduling)")
    thread: list[str] = Field(default_factory=list, description="Thread continuation posts")
    cross_post_to: list[str] = Field(default_factory=list, max_length=10, description="Additional platforms")

    @field_validator("platforms")
    @classmethod
    def validate_platforms(cls, v: list[str]) -> list[str]:
        from app.domains.postiz.platforms import PLATFORMS

        for p in v:
            if p.lower() not in PLATFORMS:
                raise ValueError(f"Unknown platform: {p}")
        return [p.lower() for p in v]


class ScheduledPost(BaseModel):
    """A scheduled or queued post."""

    id: str = Field(..., description="Post UUID")
    text: str
    platforms: list[str]
    platform_names: list[str] = Field(default_factory=list)
    media_urls: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    content_category: str = "general"
    status: PostStatus = PostStatus.draft
    scheduled_at: str | None = None
    published_at: str | None = None
    created_at: str = Field(..., description="ISO timestamp")
    updated_at: str = Field(..., description="ISO timestamp")
    timezone: str = "UTC"
    thread: list[str] = Field(default_factory=list)
    results: dict[str, str] = Field(default_factory=dict, description="Platform → post ID mapping")
    failures: dict[str, str] = Field(default_factory=dict, description="Platform → error message")
    queue_position: int | None = None


class CalendarDay(BaseModel):
    """A single day in the content calendar."""

    date: str = Field(..., description="ISO date string")
    posts: list[ScheduledPost] = Field(default_factory=list)
    post_count: int = 0
    has_gap: bool = False
    gap_days: int = 0


class CalendarResponse(BaseModel):
    """Content calendar for a date range."""

    start_date: str
    end_date: str
    days: list[CalendarDay] = Field(default_factory=list)
    total_scheduled: int = 0
    total_published: int = 0
    total_drafts: int = 0
    gaps: list[CalendarDay] = Field(default_factory=list, description="Days with no posts scheduled")
    platform_breakdown: dict[str, int] = Field(default_factory=dict)


class BulkScheduleRequest(BaseModel):
    """Bulk upload for batch scheduling."""

    entries: list[BulkScheduleEntry] = Field(..., min_length=1, max_length=1000)
    format: str = Field(default="json", description="json or csv")


class BulkScheduleEntry(BaseModel):
    """A single entry in a bulk upload."""

    text: str = Field(..., min_length=1, max_length=64000)
    platforms: list[str]
    scheduled_at: str | None = None
    media_urls: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    content_category: str = "general"


class BulkScheduleResponse(BaseModel):
    """Response from a bulk schedule operation."""

    total: int = 0
    scheduled: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    posts: list[ScheduledPost] = Field(default_factory=list)


class QueueResponse(BaseModel):
    """Content queue overview."""

    posts: list[ScheduledPost] = Field(default_factory=list)
    total: int = 0
    estimated_fill_days: float = 0.0
    optimal_interval_minutes: float = 0.0
    platform_queue: dict[str, int] = Field(default_factory=dict)


class RecurringCreateRequest(BaseModel):
    """Request to create a recurring post."""

    text: str = Field(..., min_length=1, max_length=64000, description="Post content")
    platforms: list[str] = Field(..., min_length=1, max_length=30, description="Target platform IDs")
    pattern: RecurrencePattern = Field(default=RecurrencePattern.weekly, description="Recurrence pattern")
    recurring_cron: str | None = Field(default=None, description="Custom cron expression")
    recurring_end: str | None = Field(default=None, description="Recurrence end date ISO")
    media_urls: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=30)
    content_category: str = Field(default="general")


class RecurringPost(BaseModel):
    """A recurring post template."""

    id: str = Field(..., description="Recurring post UUID")
    text: str
    platforms: list[str]
    pattern: RecurrencePattern
    cron_expression: str | None = Field(default=None, description="Cron expression for custom patterns")
    media_urls: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    content_category: str = "general"
    start_date: str = Field(..., description="ISO date")
    end_date: str | None = Field(default=None, description="End date for recurrence")
    last_posted: str | None = None
    next_post: str | None = None
    times_posted: int = 0
    status: str = "active"
    created_at: str = Field(..., description="ISO timestamp")


class GapAnalysisResponse(BaseModel):
    """Content gap analysis across platforms."""

    gaps: list[GapEntry] = Field(default_factory=list)
    platform_summary: dict[str, GapSummary] = Field(default_factory=dict)
    overall_health: str = Field(default="good", description="good, warning, critical")
    recommendations: list[str] = Field(default_factory=list)


class GapEntry(BaseModel):
    """A gap in posting for a specific platform."""

    platform_id: str
    platform_name: str
    days_since_last_post: int
    status: str = Field(default="ok", description="ok, warning, critical")
    recommended_action: str = ""


class GapSummary(BaseModel):
    """Posting gap summary for a platform."""

    last_post: str | None = None
    days_since_last: int = 0
    posts_this_week: int = 0
    posts_this_month: int = 0
    avg_posts_per_week: float = 0.0
    gap_status: str = "ok"


class BestTimeResponse(BaseModel):
    """AI-optimized best posting times per platform."""

    platform_times: dict[str, PlatformBestTime] = Field(default_factory=dict)
    overall_recommendation: str = ""
    generated_at: str = Field(..., description="ISO timestamp")


class PlatformBestTime(BaseModel):
    """Best posting times for a single platform."""

    platform_id: str
    platform_name: str
    best_time_utc: str = Field(..., description='e.g. "14:00 UTC"')
    best_day: str = Field(..., description='e.g. "Wednesday"')
    confidence: float = Field(default=0.0, ge=0, le=1.0)
    second_best_time: str = ""
    worst_time: str = ""
    peak_hours: list[int] = Field(default_factory=list)
    reasoning: str = ""


# ══════════════════════════════════════════════════════════════════════
# 3. Analytics
# ══════════════════════════════════════════════════════════════════════


class AnalyticsDashboard(BaseModel):
    """Cross-platform analytics dashboard."""

    overview: AnalyticsOverview = Field(default_factory=lambda: AnalyticsOverview())
    platforms: list[PlatformAnalytics] = Field(default_factory=list)
    best_performing: BestPerformingContent = Field(default_factory=lambda: BestPerformingContent())
    audience_growth: AudienceGrowth = Field(default_factory=lambda: AudienceGrowth())
    recommendations: list[str] = Field(default_factory=list)
    generated_at: str = Field(..., description="ISO timestamp")


class AnalyticsOverview(BaseModel):
    """Top-level analytics summary."""

    total_posts: int = 0
    total_published: int = 0
    total_impressions: int = 0
    total_engagements: int = 0
    total_followers: int = 0
    engagement_rate: float = 0.0
    avg_engagement_rate: float = 0.0
    reach: int = 0
    impressions_growth: float = 0.0
    followers_growth: float = 0.0
    active_platforms: int = 0
    posts_this_week: int = 0
    posts_this_month: int = 0


class PlatformAnalytics(BaseModel):
    """Per-platform analytics."""

    platform_id: str
    platform_name: str
    icon: str = "🌐"
    posts: int = 0
    impressions: int = 0
    engagements: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    followers: int = 0
    followers_gained: int = 0
    engagement_rate: float = 0.0
    avg_engagement_rate: float = 0.0
    reach: int = 0
    best_posting_time: str = ""
    growth_trend: list[DataPoint] = Field(default_factory=list)
    top_posts: list[PostPerformance] = Field(default_factory=list)


class PostPerformance(BaseModel):
    """Single post performance metrics."""

    post_id: str
    text_preview: str = ""
    platform: str
    published_at: str = ""
    impressions: int = 0
    engagements: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    clicks: int = 0
    engagement_rate: float = 0.0
    content_category: str = ""


class BestPerformingContent(BaseModel):
    """Best performing content analysis."""

    by_platform: dict[str, list[PostPerformance]] = Field(default_factory=dict)
    by_content_type: dict[str, float] = Field(default_factory=dict)
    by_time_of_day: dict[str, float] = Field(default_factory=dict)
    top_overall: list[PostPerformance] = Field(default_factory=list)
    insights: list[str] = Field(default_factory=list)


class AudienceGrowth(BaseModel):
    """Audience growth analytics."""

    total: int = 0
    by_platform: dict[str, int] = Field(default_factory=dict)
    daily_trend: list[DataPoint] = Field(default_factory=list)
    weekly_trend: list[DataPoint] = Field(default_factory=list)
    monthly_trend: list[DataPoint] = Field(default_factory=list)
    growth_rate: float = 0.0
    top_sources: list[dict[str, Any]] = Field(default_factory=list)


class DataPoint(BaseModel):
    """A single time-series data point."""

    date: str = ""
    value: float = 0.0
    label: str = ""


class ExportRequest(BaseModel):
    """Analytics export request."""

    format: str = Field(default="pdf", description="pdf or csv")
    period: str = Field(default="30d", description="7d, 30d, 90d, 1y")
    platforms: list[str] | None = Field(default=None, description="Filter by platforms")
    include_charts: bool = True


class ExportResponse(BaseModel):
    """Analytics export response."""

    download_url: str = ""
    format: str = ""
    generated_at: str = ""
    size_bytes: int = 0
    expires_at: str = ""


# ══════════════════════════════════════════════════════════════════════
# 4. Content Studio
# ══════════════════════════════════════════════════════════════════════


class ComposeRequest(BaseModel):
    """Request to compose a post across platforms."""

    text: str = Field(..., min_length=1, max_length=64000)
    platforms: list[str] = Field(..., min_length=1, max_length=30)
    media_ids: list[str] = Field(default_factory=list, description="Media library IDs")
    media_urls: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=30)
    content_category: str = "general"
    thread: list[str] = Field(default_factory=list)
    link: str | None = Field(default=None, description="Link to attach")
    link_title: str | None = None
    link_description: str | None = None
    link_image: str | None = None
    poll_options: list[str] = Field(default_factory=list, max_length=4)
    poll_duration_hours: int = Field(default=24, ge=1, le=168)


class ComposePreview(BaseModel):
    """Preview of how a post appears on each target platform."""

    platform_id: str
    platform_name: str
    icon: str = "🌐"
    formatted_text: str = ""
    character_count: int = 0
    max_length: int = 280
    within_limit: bool = True
    media_count: int = 0
    media_accepted: bool = True
    warnings: list[str] = Field(default_factory=list)
    preview_html: str | None = None


class ComposeResponse(BaseModel):
    """Response from composing/previewing a post."""

    previews: list[ComposePreview] = Field(default_factory=list)
    all_within_limits: bool = True
    warnings: list[str] = Field(default_factory=list)
    suggestions: HashtagSuggestions | None = None


class HashtagSuggestions(BaseModel):
    """AI-generated hashtag suggestions per platform."""

    platform_suggestions: dict[str, list[str]] = Field(default_factory=dict)
    trending_tags: list[str] = Field(default_factory=list)
    recommended: list[str] = Field(default_factory=list)


class MediaItem(BaseModel):
    """A media item in the library."""

    id: str = Field(..., description="Media UUID")
    filename: str
    url: str
    thumbnail_url: str = ""
    media_type: str = Field(..., description="image, video, document, audio")
    mime_type: str = ""
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    duration_seconds: float = 0.0
    alt_text: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(..., description="ISO timestamp")
    used_in_posts: int = 0


class MediaUploadResponse(BaseModel):
    """Response from media upload."""

    media: MediaItem
    upload_url: str = ""
    upload_expires_at: str = ""


class MediaLibraryResponse(BaseModel):
    """Media library contents."""

    items: list[MediaItem] = Field(default_factory=list)
    total: int = 0
    total_size_bytes: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)


class PostTemplate(BaseModel):
    """A saved post template."""

    id: str = Field(..., description="Template UUID")
    name: str = Field(..., description="Template name")
    description: str = ""
    text: str = ""
    platforms: list[str] = Field(default_factory=list)
    media_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    content_category: str = "general"
    variables: list[str] = Field(default_factory=list, description="Template variables like {{name}}")
    usage_count: int = 0
    created_at: str = Field(..., description="ISO timestamp")
    updated_at: str = Field(..., description="ISO timestamp")


class TemplateCreateRequest(BaseModel):
    """Request to create a post template."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    text: str = Field(..., min_length=1, max_length=64000)
    platforms: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    content_category: str = "general"


class TemplateListResponse(BaseModel):
    """List of post templates."""

    templates: list[PostTemplate] = Field(default_factory=list)
    total: int = 0
    categories: list[str] = Field(default_factory=list)
