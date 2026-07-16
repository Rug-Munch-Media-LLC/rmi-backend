"""Broadcaster - pushes fired alert events to WebSocket subscribers.

Uses core/websocket's broadcast helper. No FastAPI imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.core.websocket import broadcast_alert

if TYPE_CHECKING:
    from app.domains.alerts.models import AlertEvent

log = get_logger(__name__)


class AlertBroadcaster:
    """Thin wrapper that knows how to push an AlertEvent to live clients."""

    async def broadcast_event(self, event: AlertEvent) -> None:
        """Broadcast a single event to all connected WebSocket subscribers."""
        try:
            await broadcast_alert(event.model_dump(mode="json"))
        except Exception as e:
            # Broadcasting failure must not break the firing path.
            log.warning(
                "alert_broadcast_failed",
                error=str(e),
                alert_id=event.subscription_id,
            )
