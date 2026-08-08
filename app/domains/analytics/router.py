"""Creator Analytics — API Routes.

All endpoints at /api/v1/analytics/*.  Five analytics pillars:

  1. GET /api/v1/analytics/views                    — true view analytics (all posts)
  2. GET /api/v1/analytics/views/:post_id            — per-post view analytics
  3. GET /api/v1/analytics/engagement                — engagement analytics
  4. GET /api/v1/analytics/engagement/:post_id       — per-post engagement
  5. GET /api/v1/analytics/cross-protocol            — cross-protocol analytics
  6. GET /api/v1/analytics/cross-protocol/:post_id   — per-post cross-protocol
  7. GET /api/v1/analytics/insights                  — content improvement insights
  8. GET /api/v1/analytics/audience                  — audience analytics
  9. GET /api/v1/analytics/audience/growth           — growth analytics

  10. POST /api/v1/analytics/events/view             — ingest a view event
  11. POST /api/v1/analytics/events/engagement       — ingest an engagement event

Returns real, deduplicated metrics. No "X-style" inflation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.dependencies import get_db
from app.core.logging import get_logger
from app.domains.analytics.models import (
    AudienceSummary,
    ContentInsightsResponse,
    CrossProtocolSummary,
    EngagementAnalyticsDetail,
    EngagementAnalyticsSummary,
    GrowthAnalytics,
    ViewAnalyticsDetail,
    ViewAnalyticsSummary,
)
from app.domains.analytics.service import (
    SUPPORTED_PROTOCOLS,
    CreatorAnalyticsEngine,
    get_analytics_engine,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


# ── Request / Response Models ──────────────────────────────────────────


class RecordViewRequest(BaseModel):
    """Submit a view event for recording."""

    post_id: str = Field(..., description="Post UUID")
    creator_id: str = Field(..., description="Creator UUID")
    viewer_fingerprint: str = Field(..., description="Hashed viewer identifier")
    protocol: str = Field("nostr", description="Protocol: nostr, farcaster, bluesky, mastodon")
    watch_time_seconds: float = 0.0
    total_duration: float = 0.0
    dropped_at_seconds: float | None = None
    scrolled_past: bool = False
    scroll_depth: float = 0.0
    is_live: bool = False
    is_replay: bool = False
    session_id: str = ""
    viewer_wallet: str = ""
    referrer: str = ""


class RecordEngagementRequest(BaseModel):
    """Submit an engagement event for recording."""

    post_id: str = Field(..., description="Post UUID")
    creator_id: str = Field(..., description="Creator UUID")
    viewer_fingerprint: str = Field(..., description="Hashed viewer identifier")
    engagement_type: str = Field(..., description="like, comment, repost, tip, save, bookmark, click, follow")
    protocol: str = Field("nostr")
    tip_amount_usd: float = 0.0
    tip_token: str = ""
    tip_amount_token: float = 0.0
    comment_text: str = ""
    comment_sentiment: str = "neutral"
    sentiment_score: float = 0.0
    clicked_url: str = ""
    followed_after: bool = False


class AnalyticsOverview(BaseModel):
    """Top-level overview of all analytics for a creator dashboard."""

    views: ViewAnalyticsSummary = Field(default_factory=ViewAnalyticsSummary)
    engagement: EngagementAnalyticsSummary = Field(default_factory=EngagementAnalyticsSummary)
    cross_protocol: CrossProtocolSummary = Field(default_factory=CrossProtocolSummary)
    audience: AudienceSummary = Field(default_factory=AudienceSummary)
    insights: ContentInsightsResponse = Field(default_factory=ContentInsightsResponse)


class EventResponse(BaseModel):
    """Response for event ingestion."""

    accepted: bool = True
    message: str = "Event recorded"


# ── Deps ───────────────────────────────────────────────────────────────


def get_engine() -> CreatorAnalyticsEngine:
    """Dependency: get the analytics engine singleton."""
    return get_analytics_engine()


# ── Helper: Fetch Raw Events ──────────────────────────────────────────


async def _fetch_views(
    db: AsyncSession,
    creator_id: str | None = None,
    post_id: str | None = None,
    protocol: str | None = None,
    period_days: int = 7,
) -> list[dict[str, Any]]:
    """Fetch view events from Postgres with optional filters."""
    conditions = []
    params: dict[str, Any] = {"period": period_days}

    if creator_id:
        conditions.append("ve.creator_id = :creator_id")
        params["creator_id"] = creator_id
    if post_id:
        conditions.append("ve.post_id = :post_id")
        params["post_id"] = post_id
    if protocol:
        conditions.append("ve.protocol = :protocol")
        params["protocol"] = protocol

    where_clause = " AND ".join(conditions) if conditions else "TRUE"

    query = text(f"""
        SELECT
            ve.id, ve.post_id, ve.creator_id,
            ve.viewer_fingerprint, ve.viewer_wallet, ve.viewer_ip_hash,
            ve.session_id,
            ve.watch_time_seconds, ve.total_content_duration,
            ve.dropped_at_seconds, ve.drop_off_point_pct,
            ve.viewed_to_end, ve.scrolled_past, ve.scroll_depth_pct,
            ve.protocol, ve.is_live_view, ve.is_replay,
            ve.peak_concurrent, ve.referrer,
            ve.created_at::text as created_at,
            cp.content_type, cp.published_at::text as published_at
        FROM view_events ve
        LEFT JOIN content_posts cp ON cp.id = ve.post_id
        WHERE {where_clause}
          AND ve.created_at >= NOW() - (:period || ' days')::interval
        ORDER BY ve.created_at DESC
        LIMIT 50000
    """)  # noqa: S608 - verified bound/allowlisted SQL construction

    try:
        result = await db.execute(query, params)
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("analytics_views_fetch_error", error=str(exc))
        return []


async def _fetch_engagements(
    db: AsyncSession,
    creator_id: str | None = None,
    post_id: str | None = None,
    protocol: str | None = None,
    period_days: int = 7,
) -> list[dict[str, Any]]:
    """Fetch engagement events from Postgres."""
    conditions = []
    params: dict[str, Any] = {"period": period_days}

    if creator_id:
        conditions.append("ee.creator_id = :creator_id")
        params["creator_id"] = creator_id
    if post_id:
        conditions.append("ee.post_id = :post_id")
        params["post_id"] = post_id
    if protocol:
        conditions.append("ee.protocol = :protocol")
        params["protocol"] = protocol

    where_clause = " AND ".join(conditions) if conditions else "TRUE"

    query = text(f"""
        SELECT
            ee.id, ee.post_id, ee.creator_id,
            ee.viewer_fingerprint, ee.viewer_wallet,
            ee.engagement_type, ee.protocol,
            ee.tip_amount_usd, ee.tip_token, ee.tip_amount_token,
            ee.comment_text, ee.comment_sentiment, ee.comment_sentiment_score,
            ee.clicked_url, ee.followed_after,
            ee.created_at::text as created_at
        FROM engagement_events ee
        WHERE {where_clause}
          AND ee.created_at >= NOW() - (:period || ' days')::interval
        ORDER BY ee.created_at DESC
        LIMIT 50000
    """)  # noqa: S608 - verified bound/allowlisted SQL construction

    try:
        result = await db.execute(query, params)
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("analytics_engagements_fetch_error", error=str(exc))
        return []


async def _fetch_followers(
    db: AsyncSession,
    creator_id: str,
    period_days: int = 30,
) -> list[dict[str, Any]]:
    """Fetch audience follower records."""
    query = text("""
        SELECT
            af.id, af.creator_id,
            af.follower_fingerprint, af.follower_wallet, af.follower_handle,
            af.protocol, af.is_active,
            af.interests, af.chains_active,
            af.engagement_score, af.total_engagement_actions,
            af.total_tips_usd, af.total_views,
            af.created_at::text as created_at,
            af.unfollowed_at::text as unfollowed_at
        FROM audience_followers af
        WHERE af.creator_id = :creator_id
        ORDER BY af.created_at DESC
        LIMIT 50000
    """)

    try:
        result = await db.execute(query, {"creator_id": creator_id})
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("analytics_followers_fetch_error", error=str(exc))
        return []


async def _fetch_posts(
    db: AsyncSession,
    creator_id: str,
) -> list[dict[str, Any]]:
    """Fetch content posts for a creator."""
    query = text("""
        SELECT
            cp.id::text, cp.creator_id::text, cp.title, cp.content_type,
            cp.content_length, cp.tags,
            cp.has_images, cp.has_video, cp.video_duration_seconds, cp.has_link,
            cp.published_protocols, cp.published_at::text as published_at
        FROM content_posts cp
        WHERE cp.creator_id = :creator_id
        ORDER BY cp.published_at DESC NULLS LAST
        LIMIT 1000
    """)

    try:
        result = await db.execute(query, {"creator_id": creator_id})
        rows = result.mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("analytics_posts_fetch_error", error=str(exc))
        return []


async def _fetch_post(
    db: AsyncSession,
    post_id: str,
) -> dict[str, Any] | None:
    """Fetch a single content post."""
    query = text("""
        SELECT
            cp.id::text, cp.creator_id::text, cp.title, cp.content_type,
            cp.content_length, cp.tags, cp.published_protocols,
            cp.has_images, cp.has_video, cp.video_duration_seconds, cp.has_link,
            cp.published_at::text as published_at
        FROM content_posts cp
        WHERE cp.id = :post_id
    """)
    try:
        result = await db.execute(query, {"post_id": post_id})
        row = result.mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


# ── 1. View Analytics ─────────────────────────────────────────────────


@router.get("/views", response_model=ViewAnalyticsSummary)
async def get_view_analytics(
    creator_id: str = Query("", description="Creator UUID"),
    period: str = Query("7d", description="Period: 1h, 24h, 7d, 30d, 90d"),
    protocol: str = Query("", description="Filter by protocol"),
    db: AsyncSession = Depends(get_db),
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> dict[str, Any]:
    """GET /api/v1/analytics/views — True view analytics across all posts.

    Returns actual unique viewers, watch time, drop-off points, VTR, scroll-by rate.
    No inflation. Real numbers.
    """
    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id is required")

    period_days = _parse_period(period)
    views = await _fetch_views(db, creator_id=creator_id, protocol=protocol or None, period_days=period_days)

    return engine.compute_view_analytics(views, period_days)


@router.get("/views/{post_id}", response_model=ViewAnalyticsDetail)
async def get_post_view_analytics(
    post_id: str,
    period: str = Query("7d", description="Period: 1h, 24h, 7d, 30d, 90d"),
    db: AsyncSession = Depends(get_db),
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> dict[str, Any]:
    """GET /api/v1/analytics/views/:post_id — Per-post view analytics."""
    post = await _fetch_post(db, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    period_days = _parse_period(period)
    all_views = await _fetch_views(db, post_id=post_id, period_days=period_days)
    summary = engine.compute_view_analytics(all_views, period_days)

    # Protocol breakdown
    proto_breakdown = {}
    for proto in SUPPORTED_PROTOCOLS:
        p_views = [v for v in all_views if v.get("protocol") == proto]
        if p_views:
            proto_breakdown[proto] = engine.compute_view_analytics(p_views, period_days)

    return {
        "post_id": post_id,
        "post_title": post.get("title", ""),
        "content_type": post.get("content_type", ""),
        "published_at": post.get("published_at", ""),
        "summary": summary,
        "protocol_breakdown": proto_breakdown,
    }


# ── 2. Engagement Analytics ───────────────────────────────────────────


@router.get("/engagement", response_model=EngagementAnalyticsSummary)
async def get_engagement_analytics(
    creator_id: str = Query("", description="Creator UUID"),
    period: str = Query("7d", description="Period: 1h, 24h, 7d, 30d, 90d"),
    protocol: str = Query("", description="Filter by protocol"),
    db: AsyncSession = Depends(get_db),
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> dict[str, Any]:
    """GET /api/v1/analytics/engagement — Engagement analytics across all posts.

    Returns real likes, comments with sentiment, reposts, tips, follower gains, CTR.
    """
    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id is required")

    period_days = _parse_period(period)
    engagements = await _fetch_engagements(
        db, creator_id=creator_id, protocol=protocol or None, period_days=period_days
    )

    return engine.compute_engagement_analytics(engagements, period_days)


@router.get("/engagement/{post_id}", response_model=EngagementAnalyticsDetail)
async def get_post_engagement_analytics(
    post_id: str,
    period: str = Query("7d"),
    db: AsyncSession = Depends(get_db),
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> dict[str, Any]:
    """GET /api/v1/analytics/engagement/:post_id — Per-post engagement analytics."""
    post = await _fetch_post(db, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    period_days = _parse_period(period)
    all_engagements = await _fetch_engagements(db, post_id=post_id, period_days=period_days)
    summary = engine.compute_engagement_analytics(all_engagements, period_days)

    # Protocol breakdown
    proto_breakdown = {}
    for proto in SUPPORTED_PROTOCOLS:
        p_eng = [e for e in all_engagements if e.get("protocol") == proto]
        if p_eng:
            proto_breakdown[proto] = engine.compute_engagement_analytics(p_eng, period_days)

    # Top comments
    top_comments = [
        {
            "text": e.get("comment_text", ""),
            "sentiment": e.get("comment_sentiment", "neutral"),
            "protocol": e.get("protocol", ""),
            "created_at": str(e.get("created_at", "")),
        }
        for e in all_engagements
        if e.get("engagement_type") == "comment" and e.get("comment_text")
    ][:20]

    return {
        "post_id": post_id,
        "post_title": post.get("title", ""),
        "content_type": post.get("content_type", ""),
        "summary": summary,
        "protocol_breakdown": proto_breakdown,
        "top_comments": top_comments,
    }


# ── 3. Cross-Protocol Analytics ───────────────────────────────────────


@router.get("/cross-protocol", response_model=CrossProtocolSummary)
async def get_cross_protocol_analytics(
    creator_id: str = Query("", description="Creator UUID"),
    period: str = Query("7d"),
    db: AsyncSession = Depends(get_db),
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> dict[str, Any]:
    """GET /api/v1/analytics/cross-protocol — Compare content performance across ALL protocols.

    Shows views, engagement, and tips per protocol so creators know
    where they get the best reach and engagement.
    """
    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id is required")

    period_days = _parse_period(period)

    views_by_protocol: dict[str, list[dict[str, Any]]] = {}
    engagements_by_protocol: dict[str, list[dict[str, Any]]] = {}

    for proto in SUPPORTED_PROTOCOLS:
        views_by_protocol[proto] = await _fetch_views(
            db, creator_id=creator_id, protocol=proto, period_days=period_days
        )
        engagements_by_protocol[proto] = await _fetch_engagements(
            db, creator_id=creator_id, protocol=proto, period_days=period_days
        )

    return engine.compute_cross_protocol_analytics(views_by_protocol, engagements_by_protocol, period_days)


@router.get("/cross-protocol/{post_id}", response_model=CrossProtocolSummary)
async def get_post_cross_protocol_analytics(
    post_id: str,
    period: str = Query("7d"),
    db: AsyncSession = Depends(get_db),
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> dict[str, Any]:
    """GET /api/v1/analytics/cross-protocol/:post_id — Per-post cross-protocol analytics."""
    post = await _fetch_post(db, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    period_days = _parse_period(period)

    views_by_protocol: dict[str, list[dict[str, Any]]] = {}
    engagements_by_protocol: dict[str, list[dict[str, Any]]] = {}

    for proto in SUPPORTED_PROTOCOLS:
        views_by_protocol[proto] = await _fetch_views(db, post_id=post_id, protocol=proto, period_days=period_days)
        engagements_by_protocol[proto] = await _fetch_engagements(
            db, post_id=post_id, protocol=proto, period_days=period_days
        )

    return engine.compute_cross_protocol_analytics(views_by_protocol, engagements_by_protocol, period_days)


# ── 4. Content Improvement Insights ──────────────────────────────────


@router.get("/insights", response_model=ContentInsightsResponse)
async def get_content_insights(
    creator_id: str = Query("", description="Creator UUID"),
    db: AsyncSession = Depends(get_db),
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> dict[str, Any]:
    """GET /api/v1/analytics/insights — AI-driven content improvement insights.

    Returns data-driven patterns like:
    - 'Your posts with images get 2.3x more engagement'
    - 'Posting at 2pm UTC gets 40% more views'
    - 'Your 60-second shorts perform 3x better than 30-second'
    """
    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id is required")

    posts = await _fetch_posts(db, creator_id)

    views_by_post: dict[str, list[dict[str, Any]]] = {}
    engagements_by_post: dict[str, list[dict[str, Any]]] = {}

    for post in posts:
        pid = post.get("id", "")
        if pid:
            views_by_post[pid] = await _fetch_views(db, post_id=pid, period_days=90)
            engagements_by_post[pid] = await _fetch_engagements(db, post_id=pid, period_days=90)

    return engine.compute_content_insights(posts, views_by_post, engagements_by_post)


# ── 5. Audience Analytics ────────────────────────────────────────────


@router.get("/audience", response_model=AudienceSummary)
async def get_audience_analytics(
    creator_id: str = Query("", description="Creator UUID"),
    period: str = Query("30d"),
    db: AsyncSession = Depends(get_db),
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> dict[str, Any]:
    """GET /api/v1/analytics/audience — Audience analytics overview.

    Returns follower growth, demographics, most engaged followers, top tippers,
    churn rate, and protocol breakdown.
    """
    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id is required")

    period_days = _parse_period(period)
    followers = await _fetch_followers(db, creator_id, period_days)

    # Build follower-indexed views and engagements
    views_by_follower: dict[str, list[dict[str, Any]]] = {}
    engagements_by_follower: dict[str, list[dict[str, Any]]] = {}

    for f in followers:
        fp = f.get("follower_fingerprint", "")
        if fp:
            f_views = await _fetch_views(db, creator_id=creator_id, period_days=period_days)
            views_by_follower[fp] = [v for v in f_views if v.get("viewer_fingerprint") == fp]
            f_engagements = await _fetch_engagements(db, creator_id=creator_id, period_days=period_days)
            engagements_by_follower[fp] = [e for e in f_engagements if e.get("viewer_fingerprint") == fp]

    return engine.compute_audience_summary(followers, views_by_follower, engagements_by_follower, period_days)


@router.get("/audience/growth", response_model=GrowthAnalytics)
async def get_audience_growth(
    creator_id: str = Query("", description="Creator UUID"),
    period: str = Query("30d"),
    db: AsyncSession = Depends(get_db),
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> dict[str, Any]:
    """GET /api/v1/analytics/audience/growth — Audience growth over time.

    Returns daily, weekly, monthly growth trends and protocol-specific growth.
    """
    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id is required")

    period_days = _parse_period(period)
    followers = await _fetch_followers(db, creator_id, period_days)

    return engine.compute_growth_analytics(followers, period_days)


# ── Event Ingestion ───────────────────────────────────────────────────


@router.post("/events/view", response_model=EventResponse)
async def record_view_event(
    event: RecordViewRequest,
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> EventResponse:
    """POST /api/v1/analytics/events/view — Record a view event.

    Ingests a view event into the analytics buffer for batch processing.
    Deduplication happens via session_id + viewer_fingerprint + post_id.
    """
    await engine.record_view(
        post_id=event.post_id,
        creator_id=event.creator_id,
        viewer_fingerprint=event.viewer_fingerprint,
        protocol=event.protocol,
        watch_time_seconds=event.watch_time_seconds,
        total_duration=event.total_duration,
        dropped_at=event.dropped_at_seconds,
        scrolled_past=event.scrolled_past,
        scroll_depth=event.scroll_depth,
        is_live=event.is_live,
        is_replay=event.is_replay,
        session_id=event.session_id,
        viewer_wallet=event.viewer_wallet,
        referrer=event.referrer,
    )
    return EventResponse(accepted=True, message="View event recorded")


@router.post("/events/engagement", response_model=EventResponse)
async def record_engagement_event(
    event: RecordEngagementRequest,
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> EventResponse:
    """POST /api/v1/analytics/events/engagement — Record an engagement event.

    Ingests likes, comments, reposts, tips, saves, clicks, and follows.
    """
    await engine.record_engagement(
        post_id=event.post_id,
        creator_id=event.creator_id,
        viewer_fingerprint=event.viewer_fingerprint,
        engagement_type=event.engagement_type,
        protocol=event.protocol,
        tip_amount_usd=event.tip_amount_usd,
        tip_token=event.tip_token,
        tip_amount_token=event.tip_amount_token,
        comment_text=event.comment_text,
        comment_sentiment=event.comment_sentiment,
        sentiment_score=event.sentiment_score,
        clicked_url=event.clicked_url,
        followed_after=event.followed_after,
    )
    return EventResponse(accepted=True, message="Engagement event recorded")


# ── Dashboard Overview ────────────────────────────────────────────────


@router.get("/overview", response_model=AnalyticsOverview)
async def get_analytics_overview(
    creator_id: str = Query("", description="Creator UUID"),
    period: str = Query("7d"),
    db: AsyncSession = Depends(get_db),
    engine: CreatorAnalyticsEngine = Depends(get_engine),
) -> dict[str, Any]:
    """GET /api/v1/analytics/overview — Complete analytics dashboard overview.

    Returns views, engagement, cross-protocol, audience, and insights
    in a single response for dashboard rendering.
    """
    if not creator_id:
        raise HTTPException(status_code=400, detail="creator_id is required")

    period_days = _parse_period(period)

    # Parallel-ish data fetching (sequential due to single session)
    views_data = await _fetch_views(db, creator_id=creator_id, period_days=period_days)
    engagements_data = await _fetch_engagements(db, creator_id=creator_id, period_days=period_days)
    followers_data = await _fetch_followers(db, creator_id, period_days)

    views_by_proto: dict[str, list[dict[str, Any]]] = {}
    eng_by_proto: dict[str, list[dict[str, Any]]] = {}
    for proto in SUPPORTED_PROTOCOLS:
        views_by_proto[proto] = [v for v in views_data if v.get("protocol") == proto]
        eng_by_proto[proto] = [e for e in engagements_data if e.get("protocol") == proto]

    posts = await _fetch_posts(db, creator_id)
    views_by_post: dict[str, list[dict[str, Any]]] = {}
    engagements_by_post: dict[str, list[dict[str, Any]]] = {}
    for post in posts:
        pid = post.get("id", "")
        if pid:
            views_by_post[pid] = [v for v in views_data if str(v.get("post_id")) == pid]
            engagements_by_post[pid] = [e for e in engagements_data if str(e.get("post_id")) == pid]

    return {
        "views": engine.compute_view_analytics(views_data, period_days),
        "engagement": engine.compute_engagement_analytics(engagements_data, period_days),
        "cross_protocol": engine.compute_cross_protocol_analytics(views_by_proto, eng_by_proto, period_days),
        "audience": engine.compute_audience_summary(followers_data, {}, {}, period_days),
        "insights": engine.compute_content_insights(posts, views_by_post, engagements_by_post),
    }


# ── Helpers ───────────────────────────────────────────────────────────


def _parse_period(period: str) -> int:
    """Convert period string (1h, 24h, 7d, 30d, 90d) to days."""
    period = period.lower().strip()
    if period.endswith("h"):
        hours = int(period[:-1])
        return max(1, hours // 24) if hours >= 24 else 1
    elif period.endswith("d"):
        return int(period[:-1])
    else:
        try:
            return int(period)
        except ValueError:
            return 7
