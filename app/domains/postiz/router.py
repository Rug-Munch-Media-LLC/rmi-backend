"""Postiz Premium — API Routes.

All endpoints at /api/v1/postiz/*.  Five pillars:

PLATFORMS:
  1. GET    /api/v1/postiz/platforms                        — list all platforms
  2. POST   /api/v1/postiz/platforms/connect                — connect a platform
  3. POST   /api/v1/postiz/platforms/disconnect             — disconnect a platform

SCHEDULING:
  4. POST   /api/v1/postiz/schedule                         — schedule a post
  5. GET    /api/v1/postiz/calendar                         — content calendar
  6. POST   /api/v1/postiz/schedule/bulk                    — bulk upload
  7. GET    /api/v1/postiz/schedule/queue                   — view queue
  8. POST   /api/v1/postiz/schedule/recurring               — recurring posts
  9. GET    /api/v1/postiz/schedule/gaps                    — gap analysis
  10. GET   /api/v1/postiz/schedule/best-time               — best posting times
  11. GET   /api/v1/postiz/schedule/best-time/{platform}    — per-platform best time

ANALYTICS:
  12. GET   /api/v1/postiz/analytics                        — analytics dashboard
  13. GET   /api/v1/postiz/analytics/platform/{platform}    — per-platform analytics
  14. GET   /api/v1/postiz/analytics/export                 — export reports

CONTENT STUDIO:
  15. POST  /api/v1/postiz/compose                          — compose a post
  16. POST  /api/v1/postiz/preview                          — preview on platforms
  17. POST  /api/v1/postiz/media/upload                     — upload media
  18. GET   /api/v1/postiz/media                            — media library
  19. POST  /api/v1/postiz/templates                        — save template
  20. GET   /api/v1/postiz/templates                        — list templates

30+ platforms supported vs Hootsuite's 20 vs Buffer's 8.
Decentralized protocols: Nostr, Farcaster, Bluesky, Mastodon, Lens, PeerTube, Pixelfed, WriteFreely.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from app.core.logging import get_logger
from app.domains.postiz.models import (
    AnalyticsDashboard,
    BestTimeResponse,
    BulkScheduleEntry,
    BulkScheduleResponse,
    CalendarResponse,
    ComposePreview,
    ComposeRequest,
    ComposeResponse,
    ConnectResponse,
    ExportResponse,
    GapAnalysisResponse,
    MediaLibraryResponse,
    MediaUploadResponse,
    PlatformAnalytics,
    PlatformConnectRequest,
    PlatformListResponse,
    PostTemplate,
    QueueResponse,
    RecurringCreateRequest,
    RecurringPost,
    ScheduledPost,
    SchedulePostRequest,
    TemplateCreateRequest,
    TemplateListResponse,
)
from app.domains.postiz.service import (
    bulk_schedule,
    compose_preview,
    connect_platform,
    create_template,
    disconnect_platform,
    export_analytics,
    get_analytics_dashboard,
    get_best_times,
    get_calendar,
    get_gap_analysis,
    get_media_library,
    get_platform_analytics,
    get_platform_list,
    get_queue,
    get_templates,
    schedule_post,
    upload_media,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/postiz", tags=["postiz"])


# ── Error response ──────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    error: str
    detail: str


# ══════════════════════════════════════════════════════════════════════
# 1. PLATFORM MANAGEMENT
# ══════════════════════════════════════════════════════════════════════


@router.get(
    "/platforms",
    response_model=PlatformListResponse,
    summary="List all available platforms",
    description="Get all 30+ supported platforms grouped by category. Shows connection status for authenticated users.",
)
async def list_platforms(
    user_id: str = Query("", description="User ID to check connections for"),
) -> dict[str, Any]:
    """GET /api/v1/postiz/platforms — All available platforms with connection status."""
    return get_platform_list(user_id or None)


@router.post(
    "/platforms/connect",
    response_model=ConnectResponse,
    summary="Connect a platform",
    description="Connect a social/platform account via OAuth or API key. Returns the connection details.",
    responses={400: {"model": ErrorResponse}},
)
async def connect_platform_endpoint(
    body: PlatformConnectRequest,
) -> dict[str, Any]:
    """POST /api/v1/postiz/platforms/connect — Connect a platform account."""
    try:
        return connect_platform(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/platforms/disconnect",
    summary="Disconnect a platform",
    description="Disconnect a previously connected platform by connection ID.",
    responses={404: {"model": ErrorResponse}},
)
async def disconnect_platform_endpoint(
    connection_id: str = Query(..., description="Connection UUID to disconnect"),
) -> dict[str, Any]:
    """POST /api/v1/postiz/platforms/disconnect — Disconnect a platform."""
    try:
        return disconnect_platform(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════
# 2. SCHEDULING
# ══════════════════════════════════════════════════════════════════════


@router.post(
    "/schedule",
    response_model=ScheduledPost,
    summary="Schedule a post",
    description="Schedule a post for publishing on one or more platforms. Supports scheduling at a specific time, queueing, recurring patterns, and threads.",
    responses={400: {"model": ErrorResponse}},
)
async def schedule_post_endpoint(
    body: SchedulePostRequest,
) -> dict[str, Any]:
    """POST /api/v1/postiz/schedule — Schedule a post for publishing."""
    try:
        return schedule_post(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/calendar",
    response_model=CalendarResponse,
    summary="Content calendar",
    description="Get a visual content calendar for a date range. Shows scheduled, published, and draft posts per day.",
)
async def get_calendar_endpoint(
    start_date: str = Query(..., description="Start date (ISO format, e.g. 2026-01-01)"),
    end_date: str = Query(..., description="End date (ISO format, e.g. 2026-01-31)"),
) -> dict[str, Any]:
    """GET /api/v1/postiz/calendar — Content calendar for scheduling."""
    # Validate date format
    import re

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", start_date) or not re.match(r"^\d{4}-\d{2}-\d{2}$", end_date):
        raise HTTPException(status_code=400, detail="Dates must be in YYYY-MM-DD format")
    return get_calendar(start_date, end_date)


@router.post(
    "/schedule/bulk",
    response_model=BulkScheduleResponse,
    summary="Bulk schedule posts",
    description="Upload multiple posts at once via JSON or CSV. Supports up to 1000 entries per batch.",
)
async def bulk_schedule_endpoint(
    body: list[BulkScheduleEntry],
) -> dict[str, Any]:
    """POST /api/v1/postiz/schedule/bulk — Batch schedule posts from JSON array."""
    entries = [e.model_dump() for e in body]
    return bulk_schedule(entries)


@router.get(
    "/schedule/queue",
    response_model=QueueResponse,
    summary="View posting queue",
    description="View the auto-posting queue. Shows queued posts, estimated fill time, and optimal intervals.",
)
async def get_queue_endpoint() -> dict[str, Any]:
    """GET /api/v1/postiz/schedule/queue — View the posting queue."""
    return get_queue()


@router.post(
    "/schedule/recurring",
    response_model=RecurringPost,
    summary="Create recurring post",
    description="Create a recurring post schedule (daily, weekly, monthly, or custom cron). Auto-posts at the defined interval.",
)
async def create_recurring_endpoint(
    body: RecurringCreateRequest,
) -> dict[str, Any]:
    """POST /api/v1/postiz/schedule/recurring — Create a recurring post schedule."""
    from app.domains.postiz.service import create_recurring as svc_create_recurring

    try:
        return svc_create_recurring(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/schedule/gaps",
    response_model=GapAnalysisResponse,
    summary="Posting gap analysis",
    description="Analyze posting frequency across all connected platforms. Shows days since last post per platform with warnings and recommendations.",
)
async def get_gaps_endpoint() -> dict[str, Any]:
    """GET /api/v1/postiz/schedule/gaps — Analyze posting gaps."""
    return get_gap_analysis()


@router.get(
    "/schedule/best-time",
    response_model=BestTimeResponse,
    summary="Best posting times (all platforms)",
    description="AI-optimized best posting times for all supported platforms. Data-driven recommendations per platform.",
)
async def get_best_times_endpoint() -> dict[str, Any]:
    """GET /api/v1/postiz/schedule/best-time — Best posting times for all platforms."""
    return get_best_times()


@router.get(
    "/schedule/best-time/{platform_id}",
    response_model=BestTimeResponse,
    summary="Best posting time (single platform)",
    description="Get the AI-optimized best posting time for a specific platform.",
    responses={404: {"model": ErrorResponse}},
)
async def get_platform_best_time_endpoint(
    platform_id: str = Path(..., description="Platform ID (e.g. 'farcaster', 'nostr')"),
) -> dict[str, Any]:
    """GET /api/v1/postiz/schedule/best-time/{platform} — Per-platform best posting time."""
    from app.domains.postiz.platforms import get_platform

    pdef = get_platform(platform_id)
    if not pdef:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform_id}")

    data = get_best_times()
    platform_data = data["platform_times"].get(platform_id)
    if not platform_data:
        raise HTTPException(status_code=404, detail=f"No timing data for platform: {platform_id}")

    return {
        "platform_times": {platform_id: platform_data},
        "overall_recommendation": f"Best time to post on {pdef.name} is {platform_data['best_time_utc']} on {platform_data['best_day']}s",
        "generated_at": data["generated_at"],
    }


# ══════════════════════════════════════════════════════════════════════
# 3. ANALYTICS
# ══════════════════════════════════════════════════════════════════════


@router.get(
    "/analytics",
    response_model=AnalyticsDashboard,
    summary="Analytics dashboard",
    description="Cross-platform analytics dashboard. Compare performance across all 30+ platforms with engagement rates, audience growth, and recommendations.",
)
async def get_analytics_endpoint(
    period: str = Query("30d", description="Period: 7d, 30d, 90d, 1y"),
) -> dict[str, Any]:
    """GET /api/v1/postiz/analytics — Cross-platform analytics dashboard."""
    return get_analytics_dashboard(period)


@router.get(
    "/analytics/platform/{platform_id}",
    response_model=PlatformAnalytics,
    summary="Per-platform analytics",
    description="Detailed analytics for a single platform including posts, impressions, engagement, follower growth, and top performing posts.",
    responses={404: {"model": ErrorResponse}},
)
async def get_platform_analytics_endpoint(
    platform_id: str = Path(..., description="Platform ID (e.g. 'twitter', 'nostr')"),
    period: str = Query("30d", description="Period: 7d, 30d, 90d, 1y"),
) -> dict[str, Any]:
    """GET /api/v1/postiz/analytics/platform/{platform} — Per-platform analytics."""
    try:
        return get_platform_analytics(platform_id, period)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get(
    "/analytics/export",
    response_model=ExportResponse,
    summary="Export analytics report",
    description="Export analytics data as PDF or CSV. Includes charts, platform breakdown, and performance insights.",
)
async def export_analytics_endpoint(
    export_format: str = Query("pdf", description="pdf or csv"),
    period: str = Query("30d", description="7d, 30d, 90d, 1y"),
    platforms: str = Query("", description="Comma-separated platform IDs to include"),
    include_charts: bool = Query(True, description="Include charts in export"),
) -> dict[str, Any]:
    """GET /api/v1/postiz/analytics/export — Export analytics report."""
    platform_list = [p.strip() for p in platforms.split(",") if p.strip()] if platforms else None
    return export_analytics(export_format, period, platform_list, include_charts)


# ══════════════════════════════════════════════════════════════════════
# 4. CONTENT STUDIO
# ══════════════════════════════════════════════════════════════════════


@router.post(
    "/compose",
    response_model=ComposeResponse,
    summary="Compose a post with preview",
    description="Compose a post and see how it renders on each target platform. Includes character counts, media checks, and hashtag suggestions.",
)
async def compose_post_endpoint(
    body: ComposeRequest,
) -> dict[str, Any]:
    """POST /api/v1/postiz/compose — Compose and preview a post across platforms."""
    return compose_preview(body.model_dump())


@router.post(
    "/preview",
    response_model=list[ComposePreview],
    summary="Preview post on platforms",
    description="Preview how a post will appear on each selected platform. Shows character counts, warnings, and platform-specific formatting.",
)
async def preview_post_endpoint(
    body: ComposeRequest,
) -> list[dict[str, Any]]:
    """POST /api/v1/postiz/preview — Preview post on specific platforms."""
    result = compose_preview(body.model_dump())
    return result["previews"]


@router.post(
    "/media/upload",
    response_model=MediaUploadResponse,
    summary="Upload media",
    description="Upload media (images, videos, documents) to the media library for use across all platforms.",
)
async def upload_media_endpoint(
    filename: str = Query(..., description="Filename with extension"),
    media_type: str = Query(..., description="image, video, document, audio"),
    mime_type: str = Query("application/octet-stream", description="MIME type"),
    size_bytes: int = Query(0, ge=0, description="File size in bytes"),
) -> dict[str, Any]:
    """POST /api/v1/postiz/media/upload — Upload media to the library."""
    return upload_media(filename, media_type, mime_type, size_bytes)


@router.get(
    "/media",
    response_model=MediaLibraryResponse,
    summary="Media library",
    description="Browse the media library with all uploaded images, videos, and documents. Filterable by media type.",
)
async def get_media_library_endpoint(
    media_type: str = Query("", description="Filter by media type (image, video, document, audio)"),
    limit: int = Query(50, ge=1, le=200, description="Results per page"),
    offset: int = Query(0, ge=0, description="Page offset"),
) -> dict[str, Any]:
    """GET /api/v1/postiz/media — Browse the media library."""
    return get_media_library(
        media_type=media_type or None,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/templates",
    response_model=PostTemplate,
    summary="Create post template",
    description="Save a post template for reuse. Templates can include variables like {{name}} for personalization.",
)
async def create_template_endpoint(
    body: TemplateCreateRequest,
) -> dict[str, Any]:
    """POST /api/v1/postiz/templates — Save a post template."""
    return create_template(body.model_dump())


@router.get(
    "/templates",
    response_model=TemplateListResponse,
    summary="List post templates",
    description="Get all saved post templates grouped by category.",
)
async def list_templates_endpoint() -> dict[str, Any]:
    """GET /api/v1/postiz/templates — List all post templates."""
    return get_templates()
