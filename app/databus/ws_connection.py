"""DataBus WebSocket connection registry — channel fan-out and tier gating.

Split out of ``app.databus.ws_stream`` (2026-10-25): the registry has no
dependency on the FastAPI router, so it can be imported and unit-tested alone.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = get_logger("databus.ws_connection")

TIER_ORDER = {"free": 0, "basic": 1, "premium": 2, "enterprise": 3}


class WSConnectionManager:
    """Manages WebSocket connections by channel."""

    def __init__(self) -> None:
        # channel → set of (websocket, tier)
        self._connections: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
        self._total_messages = 0
        self._total_connections = 0

    async def connect(self, ws: WebSocket, channel: str, tier: str = "free") -> None:
        await ws.accept()
        self._connections[channel].add((ws, tier))
        self._total_connections += 1
        logger.info("databus_ws_connected", channel=channel, tier=tier)

    def disconnect(self, ws: WebSocket, channel: str) -> None:
        self._connections[channel].discard((ws, _tier_for_ws(ws, channel)))
        logger.info("databus_ws_disconnected", channel=channel)

    async def broadcast(self, channel: str, data: dict[str, Any], min_tier: str = "free") -> None:
        """Broadcast data to all connections on a channel.
        Only sends to connections with tier >= min_tier.
        """
        min_level = TIER_ORDER.get(min_tier, 0)
        dead = []
        for ws, tier in list(self._connections.get(channel, set())):
            if TIER_ORDER.get(tier, 0) < min_level:
                continue
            try:
                await ws.send_json(data)
                self._total_messages += 1
            except (ConnectionError, OSError, TimeoutError) as exc:
                logger.warning("databus_ws_broadcast_failed", channel=channel, tier=tier, error=str(exc))
                dead.append((ws, tier))
        for ws, tier in dead:
            self._connections[channel].discard((ws, tier))

    async def broadcast_all(self, data: dict[str, Any], channel: str = "") -> None:
        """Broadcast to 'all' channel (receives everything)."""
        await self.broadcast("all", data)
        if channel:
            await self.broadcast(channel, data)

    def stats(self) -> dict[str, Any]:
        return {
            "channels": {ch: len(conns) for ch, conns in self._connections.items()},
            "total_connections": self._total_connections,
            "total_messages_sent": self._total_messages,
        }


def _tier_for_ws(ws: WebSocket, channel: str) -> str:
    """Extract tier from WS query params (fallback)."""
    return "free"


# ── Singleton ────────────────────────────────────────────────────────

ws_manager = WSConnectionManager()
