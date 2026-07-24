"""
DataBus WebSocket Stream - Real-time Data Push
================================================

WebSocket endpoint that pushes real-time data updates to connected clients.
Channels: prices, alerts, whales, smart_money, market_overview, all

Clients connect to: ws://host/api/v1/databus/ws/{channel}

Premium feature - requires x402 payment or subscription for access.
Free tier gets read-only access to 'prices' and 'market_overview' channels.

Author: RMI Development
Date: 2026-06-02
"""

import asyncio
import json
import time
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import get_logger
from typing import Any

logger = get_logger("databus.ws_stream")

router = APIRouter(tags=["databus-websocket"])

# ── Connection Manager ──────────────────────────────────────────────


class WSConnectionManager:
    """Manages WebSocket connections by channel."""

    def __init__(self) -> None:
        # channel → set of (websocket, tier)
        self._connections: dict[str, set[tuple]] = defaultdict(set)
        self._total_messages = 0
        self._total_connections = 0

    async def connect(self, ws: WebSocket, channel: str, tier: str = "free") -> None:
        await ws.accept()
        self._connections[channel].add((ws, tier))
        self._total_connections += 1
        logger.info(f"WS connected: channel={channel}, tier={tier}")

    def disconnect(self, ws: WebSocket, channel: str) -> None:
        self._connections[channel].discard((ws, _tier_for_ws(ws, channel)))
        logger.info(f"WS disconnected: channel={channel}")

    async def broadcast(self, channel: str, data: dict, min_tier: str = "free") -> None:
        """Broadcast data to all connections on a channel.
        Only sends to connections with tier >= min_tier.
        """
        tier_order = {"free": 0, "basic": 1, "premium": 2, "enterprise": 3}
        min_level = tier_order.get(min_tier, 0)
        dead = []
        for ws, tier in list(self._connections.get(channel, set())):
            if tier_order.get(tier, 0) < min_level:
                continue
            try:
                await ws.send_json(data)
                self._total_messages += 1
            except (ConnectionError, OSError, TimeoutError):  # noqa: BLE001
                dead.append((ws, tier))
        for ws, tier in dead:
            self._connections[channel].discard((ws, tier))

    async def broadcast_all(self, data: dict, channel: str = "") -> None:
        """Broadcast to 'all' channel (receives everything)."""
        await self.broadcast("all", data)
        if channel:
            await self.broadcast(channel, data)

    def stats(self) -> dict:
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


# ── DataBus Integration Hook ────────────────────────────────────────

# This function is called by DataBus core when it broadcasts data
# It pushes the data to websocket clients on the matching channel

CHANNEL_MAP = {
    "token_price": "prices",
    "alerts": "alerts",
    "entity_intel": "whales",
    "smart_money": "smart_money",
    "market_overview": "market_overview",
    "trending": "prices",
    "market_movers": "prices",
}


async def databus_ws_broadcast(data_type: str, result: dict) -> None:
    """Hook called by DataBus core to push real-time updates to WS clients."""
    channel = CHANNEL_MAP.get(data_type)
    if channel:
        payload = {
            "channel": channel,
            "data_type": data_type,
            "data": result.get("data"),
            "timestamp": result.get("latency_ms", 0),
        }
        await ws_manager.broadcast_all(payload, channel)


# ── WebSocket Endpoint ───────────────────────────────────────────────


@router.websocket("/api/v1/databus/ws/{channel}")
async def databus_websocket(ws: WebSocket, channel: str) -> None:
    """
    Connect to a real-time data stream.

    Channels:
      - prices: token price updates, trending, movers
      - alerts: rug pull alerts, whale movements, new launches
      - whales: whale tracking, large transactions
      - smart_money: smart money moves, profitable traders
      - market_overview: aggregate market stats
      - all: everything (premium+ only)

    Tier levels (via query param ?tier=basic):
      - free: prices + market_overview only
      - basic: + alerts
      - premium: + whales + smart_money
      - enterprise: all
    """
    valid_channels = {"prices", "alerts", "whales", "smart_money", "market_overview", "all"}
    if channel not in valid_channels:
        await ws.close(code=4000, reason=f"Invalid channel. Use: {', '.join(valid_channels)}")
        return

    tier = ws.query_params.get("tier", "free").lower()

    # Free tier can only access prices and market_overview
    free_allowed = {"prices", "market_overview"}
    if tier == "free" and channel not in free_allowed:
        await ws.close(code=4001, reason=f"Channel '{channel}' requires basic+ tier")
        return

    await ws_manager.connect(ws, channel, tier)
    try:
        # Send initial confirmation
        await ws.send_json(
            {
                "type": "connected",
                "channel": channel,
                "tier": tier,
                "message": f"Subscribed to {channel} stream ({tier} tier)",
            }
        )

        # Keep connection alive - listen for pings
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=60)
                # Client can send {"type": "ping"} for keepalive
                if data.strip() == "ping" or json.loads(data).get("type") == "ping":
                    await ws.send_json({"type": "pong", "ts": int(time.time())})
            except TimeoutError:
                # No message for 60s - send keepalive ping
                try:
                    await ws.send_json({"type": "ping", "ts": int(time.time())})
                except (json.JSONDecodeError):  # noqa: BLE001
                    break
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(ws, channel)


# ── Stats Endpoint ───────────────────────────────────────────────────


@router.get("/api/v1/databus/ws/stats")
async def ws_stats() -> Any:
    """WebSocket connection stats."""
    return ws_manager.stats()
