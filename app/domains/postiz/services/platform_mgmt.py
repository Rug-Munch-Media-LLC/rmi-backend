from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.domains.postiz.platforms import PLATFORMS, get_decentralized_platforms, get_platform
from app.domains.postiz.services.store import get_store

logger = get_logger(__name__)


def get_platform_list(user_id: str | None = None) -> dict[str, Any]:
    """Get all platforms with optional connection status for a user."""
    store = get_store()
    connected_ids = store.get_connected_platform_ids()

    platforms_list: list[dict[str, Any]] = []
    for pid, pdef in PLATFORMS.items():
        is_connected = pid in connected_ids
        conn = None
        if is_connected:
            conns = store.get_connections_by_platform(pid)
            conn = conns[0] if conns else None

        platforms_list.append(
            {
                "id": pid,
                "name": pdef.name,
                "category": pdef.category,
                "max_length": pdef.max_length,
                "media_types": pdef.media_types,
                "decentralized": pdef.decentralized,
                "icon": pdef.icon,
                "status": pdef.status,
                "connected": is_connected,
                "connection_id": conn.get("id") if conn else None,
                "account_name": conn.get("account_name") if conn else None,
            }
        )

    categories: dict[str, int] = {}
    for p in platforms_list:
        categories[p["category"]] = categories.get(p["category"], 0) + 1

    return {
        "platforms": platforms_list,
        "total": len(platforms_list),
        "connected_count": len(connected_ids),
        "decentralized_count": len(get_decentralized_platforms()),
        "categories": categories,
    }


def connect_platform(request_data: dict[str, Any]) -> dict[str, Any]:
    """Connect a platform account."""
    platform_id = request_data.get("platform_id", "").lower()
    pdef = get_platform(platform_id)
    if not pdef:
        raise ValueError(f"Unknown platform: {platform_id}")

    store = get_store()

    # In production: exchange OAuth code, store tokens securely
    connection = store.add_connection(
        {
            "platform_id": platform_id,
            "platform_name": pdef.name,
            "account_name": request_data.get("label", f"@{platform_id}_user"),
            "account_id": f"{platform_id}_acc_{uuid.uuid4().hex[:8]}",
            "avatar_url": "",
            "expires_at": None,
            "label": request_data.get("label", ""),
            "status": "active",
            "rate_limit_remaining": 1000,
            "rate_limit_reset": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
    )

    return {
        "success": True,
        "connection": connection,
        "message": f"{pdef.name} connected successfully",
        "onboarding_url": None,
    }


def disconnect_platform(connection_id: str) -> dict[str, Any]:
    """Disconnect a platform."""
    store = get_store()
    if not store.remove_connection(connection_id):
        raise ValueError(f"Connection not found: {connection_id}")
    return {"success": True, "message": "Platform disconnected"}


# ══════════════════════════════════════════════════════════════════════
# 2. Scheduling Service
# ══════════════════════════════════════════════════════════════════════
