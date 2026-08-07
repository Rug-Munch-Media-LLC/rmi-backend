"""Postiz Premium — Deprecated facade.

All logic has moved to app.domains.postiz.services.
Import from there directly.
"""

from __future__ import annotations

from app.domains.postiz.services import (
    PostizStore,
    bulk_schedule,
    compose_preview,
    connect_platform,
    create_recurring,
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
    get_store,
    get_templates,
    schedule_post,
    upload_media,
)

# Explicit re-export of the facade API so mypy/importers see these as
# intentionally exported (they are defined in app.domains.postiz.services).
__all__ = [
    "PostizStore",
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
