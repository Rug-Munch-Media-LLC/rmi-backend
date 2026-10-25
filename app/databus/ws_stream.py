"""
DataBus WebSocket Stream - Real-time Data Push
================================================

WebSocket endpoint that pushes real-time data updates to connected clients.
Channels: prices, alerts, whales, smart_money, market_overview, all

Clients connect to: ws://host/api/v1/databus/ws/{channel}

Premium feature - requires x402 payment or subscription for access.
Free tier gets read-only access to 'prices' and 'market_overview' channels.

The connection registry lives in ``app.databus.ws_connection``.

Author: RMI Development
Date: 2026-06-02
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import get_logger
from app.databus.ws_connection import WSConnectionManager, ws_manager

__all__ = [
    "CHANNEL_MAP",
    "WSConnectionManager",
    "databus_websocket",
    "databus_ws_broadcast",
    "is_ping",
    "router",
    "ws_manager",
    "ws_stats",
]

logger = get_logger("databus.ws_stream")

router = APIRouter(tags=["databus-websocket"])

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

VALID_CHANNELS = {"prices", "alerts", "whales", "smart_money", "market_overview", "all"}
FREE_CHANNELS = {"prices", "market_overview"}
PING_TIMEOUT_SECONDS = 60


async def databus_ws_broadcast(data_type: str, result: dict[str, Any]) -> None:
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


def is_ping(raw: str) -> bool:
    """True when a client frame is a keepalive ping.

    Accepts the bare token ``ping`` and the JSON form ``{"type": "ping"}``.
    Malformed JSON is not a ping — it must not kill the socket.
    """
    if raw.strip() == "ping":
        return True
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("type") == "ping"


async def _keepalive(ws: WebSocket, channel: str) -> bool:
    """Send one keepalive ping. False when the socket is dead, so stop the loop.

    A send failure is terminal for this connection: the registry unsubscribes it
    in the endpoint's ``finally`` block.
    """
    try:
        await ws.send_json({"type": "ping", "ts": int(time.time())})
    except (ConnectionError, OSError, RuntimeError) as exc:
        logger.warning("databus_ws_keepalive_failed", channel=channel, error=str(exc))
        return False
    return True


async def _receive_loop(ws: WebSocket, channel: str) -> None:
    """Answer pings and emit keepalives until the client goes away."""
    while True:
        try:
            data = await asyncio.wait_for(ws.receive_text(), timeout=PING_TIMEOUT_SECONDS)
            # Client can send {"type": "ping"} for keepalive
            if is_ping(data):
                await ws.send_json({"type": "pong", "ts": int(time.time())})
        except TimeoutError:
            # No message for 60s - send keepalive ping
            if not await _keepalive(ws, channel):
                break
        except WebSocketDisconnect:
            break


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
    if channel not in VALID_CHANNELS:
        await ws.close(code=4000, reason=f"Invalid channel. Use: {', '.join(VALID_CHANNELS)}")
        return

    tier = ws.query_params.get("tier", "free").lower()

    # Free tier can only access prices and market_overview
    if tier == "free" and channel not in FREE_CHANNELS:
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
        await _receive_loop(ws, channel)
    except WebSocketDisconnect as exc:
        logger.warning("databus_websocket_disconnected", channel=channel, error=str(exc))
    finally:
        ws_manager.disconnect(ws, channel)


# ── Stats Endpoint ───────────────────────────────────────────────────


@router.get("/api/v1/databus/ws/stats")
async def ws_stats() -> Any:
    """WebSocket connection stats."""
    return ws_manager.stats()
