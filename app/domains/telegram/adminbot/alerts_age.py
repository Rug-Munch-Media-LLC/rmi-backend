"""Max-age gate for queued admin alerts.

A queue that was not consumed for days must not drain into Telegram: one
flush of week-old events is worse than dropping them, because it arrives
with no context and trains the operator to ignore the channel. The gate is
on **queue residence time** (``queuedAt``, stamped at push), never on the
alert's ``startsAt`` -- a genuinely ongoing incident keeps its original
start time across every Alertmanager repeat and must still be delivered.

Undated payloads pass: staleness that cannot be proven is not suppressed.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger("admin_bot.alerts_feed")

MAX_ALERT_AGE_SECONDS = int(os.getenv("ALERTS_FEED_MAX_AGE", "1800"))
_QUEUED_AT = "queuedAt"


def is_stale(alert: dict[str, Any], now: datetime | None = None) -> bool:
    """True when this queued alert is older than MAX_ALERT_AGE_SECONDS."""
    raw = alert.get(_QUEUED_AT) or alert.get("queued_at")
    if not raw:
        return False
    try:
        queued = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if queued.tzinfo is None:
        queued = queued.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    age_seconds = (current - queued).total_seconds()
    if age_seconds <= MAX_ALERT_AGE_SECONDS:
        return False
    labels = alert.get("labels") or {}
    logger.warning(
        "alert_dropped_stale name=%s age_s=%d max_age_s=%d queuedAt=%s",
        labels.get("alertname", "unknown"),
        int(age_seconds),
        MAX_ALERT_AGE_SECONDS,
        raw,
    )
    return True
