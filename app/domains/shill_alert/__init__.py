"""Coordinated-shill alert domain: ShillPost → PlatformAlert → routing.

Pure wiring of the ``shill_detector`` rules into the canonical platform-alert
pipeline. ``ShillPost`` normalizes Telegram / X / Farcaster posts,
``run_shill_detection`` emits ``PlatformAlert`` (category=syndicate) for
coordinated shill clusters, and ``route`` produces the channel routing plan
(rmialerts = high+, main_channel = critical).
"""

from __future__ import annotations

from app.domains.shill_alert.models import ShillPost
from app.domains.shill_alert.service import (
    group_by_author,
    ingest_telegram_posts,
    route,
    run_shill_detection,
)

__all__ = [
    "ShillPost",
    "group_by_author",
    "ingest_telegram_posts",
    "route",
    "run_shill_detection",
]
