"""Supabase analytics: subscriptions and event tracking."""

from __future__ import annotations

from typing import Any

from app.services.supabase_transport import (
    _get,
    _post,
)


async def get_subscription(user_id: str) -> dict[str, Any] | None:
    data = await _get("subscriptions", {"user_id": user_id}, limit=1)
    return data[0] if data else None


async def track_event(
    event_type: str,
    user_id: str | None = None,
    event_data: dict[str, Any] | None = None,
    page: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    return await _post(
        "analytics_events",
        {
            "event_type": event_type,
            "user_id": user_id,
            "event_data": event_data or {},
            "page": page,
            "session_id": session_id,
        },
    )
