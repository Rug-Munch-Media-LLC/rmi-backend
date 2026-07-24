"""Broadcaster - pushes fired alert events to WebSocket subscribers + event bus.

Uses the EventBus (core/events.py) for async pub/sub dispatch AND the
caching shield's WebSocket manager for live client push. No FastAPI imports.

Data flow::
  AlertService.fire_event() → AlertBroadcaster.broadcast_event()
    ├──→ EventBus.publish(AlertTriggered)    # async decoupled consumers
    └──→ WsClientManager.broadcast_alert()   # live WebSocket push
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.events import AlertTriggered, get_event_bus
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domains.alerts.models import AlertEvent

log = get_logger(__name__)


class AlertBroadcaster:
    """Pushes an AlertEvent to both the event bus and live WebSocket clients."""

    async def broadcast_event(self, event: AlertEvent) -> None:
        """Broadcast to event bus subscribers + live WebSocket connections."""
        try:
            # 1. Publish to event bus for async consumers
            bus = get_event_bus()
            await bus.publish(
                AlertTriggered(
                    alert_id=event.subscription_id,
                    token_address=event.token_address,
                    event_type=event.event_type.value,
                    severity=event.severity,
                    payload=event.payload,
                )
            )

            # 2. Push to live WebSocket clients via caching shield
            try:
                from app.caching_shield.ws_broadcaster import get_ws_manager

                manager = get_ws_manager()
                await manager.broadcast_alert(event.model_dump(mode="json"))
            except ImportError:
                log.warning("ws_manager_not_available")
            except Exception as exc:
                log.warning("ws_broadcast_failed", error=str(exc))

        except (ValueError, TypeError, KeyError, OSError) as exc:
            # Broadcasting failure must not break the firing path.
            log.warning(
                "alert_broadcast_failed",
                error=str(exc),
                alert_id=event.subscription_id,
            )
