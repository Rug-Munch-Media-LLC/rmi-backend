from __future__ import annotations

from app.domains.postiz.services.analytics import (
    _compute_audience_growth,
    _compute_best_performing,
    _generate_analytics_recommendations,
    _get_top_posts_for_platform,
    export_analytics,
    get_analytics_dashboard,
    get_platform_analytics,
)
from app.domains.postiz.services.best_times import get_best_times
from app.domains.postiz.services.content_studio import (
    _format_for_platform,
    _generate_hashtag_suggestions,
    compose_preview,
    get_media_library,
    upload_media,
)
from app.domains.postiz.services.gap_analysis import (
    _generate_gap_recommendations,
    _get_gap_action,
    get_gap_analysis,
)
from app.domains.postiz.services.platform_mgmt import (
    connect_platform,
    disconnect_platform,
    get_platform_list,
)
from app.domains.postiz.services.scheduling import (
    bulk_schedule,
    create_recurring,
    get_calendar,
    get_queue,
    schedule_post,
)
from app.domains.postiz.services.store import PostizStore, get_store
from app.domains.postiz.services.templates import (
    _extract_template_variables,
    _generate_trend,
    _parse_period,
    _simulate_metric,
    create_template,
    get_templates,
)

__all__ = [
    "PostizStore",
    "_compute_audience_growth",
    "_compute_best_performing",
    "_extract_template_variables",
    "_format_for_platform",
    "_generate_analytics_recommendations",
    "_generate_gap_recommendations",
    "_generate_hashtag_suggestions",
    "_generate_trend",
    "_get_gap_action",
    "_get_top_posts_for_platform",
    "_parse_period",
    "_simulate_metric",
    "bulk_schedule",
    "compose_preview",
    "connect_platform",
    "create_recurring",
    "create_template",
    "disconnect_platform",
    "export_analytics",
    "get_analytics_dashboard",
    "get_best_times",
    "get_calendar",
    "get_gap_analysis",
    "get_media_library",
    "get_platform_analytics",
    "get_platform_list",
    "get_queue",
    "get_store",
    "get_templates",
    "schedule_post",
    "upload_media",
]
