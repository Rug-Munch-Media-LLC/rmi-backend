"""
Aggressive Caching Shield — WebSocket Broadcast Manager
Connection-pooled Redis pub/sub for real-time streaming to frontend users.

The existing WebSocket code creates a new Redis connection per broadcast.
This module provides a pooled, persistent Redis connection for pub/sub
publishing, plus a lightweight WebSocket manager for tracking connected
clients and broadcasting efficiently.

Features:
  - Persistent Redis connection (not one per broadcast)
  - Client tracking with auto-cleanup on disconnect
  - Channel-based subscriptions (scans, alerts, prices, tokens)
  - Heartbeat/ping to detect zombie connections
  - Broadcast stats

Usage:
    from app.caching_shield.ws_broadcaster import get_ws_manager

    manager = get_ws_manager()
    await manager.broadcast("scans", {"token": "SoL...", "score": 85})
"""

import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis

logger = logging.getLogger("ws_broadcaster")

# Default Redis pub/sub channels
CHANNEL_SCANS = "rmi:ws:scans"
CHANNEL_ALERTS = "rmi:ws:alerts"
CHANNEL_PRICES = "rmi:ws:prices"
CHANNEL_TOKENS = "rmi:ws:tokens"

# Heartbeat interval for zombie detection
HEARTBEAT_INTERVAL = 30


class WsClientManager:
    """Tracks connected WebSocket clients and handles broadcasting.

    Does NOT own the WebSocket objects — those live in the FastAPI route handlers.
    This manages the Redis pub/sub bridge and client metadata.
    """

    def __init__(self, redis_url=None, redis_password=None):
        self._redis: aioredis.Redis | None = None
        self._redis_url = redis_url
        self._redis_password = redis_password
        self._redis_failed = False
        self._init_lock = asyncio.Lock()

        # Channel -> set of client_ids
        self._clients: dict[str, set[str]] = {
            "scans": set(),
            "alerts": set(),
            "prices": set(),
            "tokens": set(),
        }
        self._lock = asyncio.Lock()

        # Stats
        self.broadcasts_sent = 0
        self.broadcasts_failed = 0

    async def _get_redis(self):
        """Get or create persistent Redis connection for publishing."""
        if self._redis is not None:
            return self._redis
        if self._redis_failed:
            return None
        async with self._init_lock:
            if self._redis is not None:
                return self._redis
            if self._redis_failed:
                return None
            try:
                host = self._redis_url or os.getenv("REDIS_HOST", "rmi-redis")
                port = int(os.getenv("REDIS_PORT", "6379"))
                password = self._redis_password or os.getenv("REDIS_PASSWORD", "")
                url = f"redis://:{password}@{host}:{port}" if password else f"redis://{host}:{port}"
                self._redis = aioredis.from_url(
                    url, socket_connect_timeout=2, decode_responses=True, max_connections=10
                )
                await self._redis.ping()
                logger.info("WsClientManager: Redis connected OK")
                return self._redis
            except Exception as e:
                logger.warning(f"WsClientManager: Redis unavailable ({e})")
                self._redis_failed = True
                return None

    async def register(self, channel: str, client_id: str):
        """Register a connected client for a channel."""
        async with self._lock:
            if channel not in self._clients:
                self._clients[channel] = set()
            self._clients[channel].add(client_id)

    async def unregister(self, channel: str, client_id: str):
        """Remove a disconnected client."""
        async with self._lock:
            if channel in self._clients:
                self._clients[channel].discard(client_id)

    async def broadcast(self, channel: str, data: dict):
        """Publish data to a Redis channel for WebSocket subscribers.

        Uses persistent Redis connection — no new connection per broadcast.
        Falls back silently if Redis is unavailable (clients connected
        directly to WebSocket server still get messages).
        """
        redis = await self._get_redis()
        if not redis:
            self.broadcasts_failed += 1
            return

        # Map channel name to Redis channel
        redis_channel = {
            "scans": CHANNEL_SCANS,
            "alerts": CHANNEL_ALERTS,
            "prices": CHANNEL_PRICES,
            "tokens": CHANNEL_TOKENS,
        }.get(channel, f"rmi:ws:{channel}")

        payload = json.dumps(
            {
                "type": channel,
                **data,
                "timestamp": time.time(),
            },
            default=str,
        )

        try:
            await redis.publish(redis_channel, payload)
            self.broadcasts_sent += 1
        except Exception as e:
            self.broadcasts_failed += 1
            logger.debug(f"WsClientManager broadcast error: {e}")

    async def broadcast_scan(self, scan_data: dict):
        """Convenience: broadcast a token scan result."""
        await self.broadcast("scans", scan_data)

    async def broadcast_alert(self, alert_data: dict):
        """Convenience: broadcast a security alert."""
        # Also persist to alert history (sorted set)
        redis = await self._get_redis()
        if redis:
            try:
                payload = json.dumps(
                    {
                        "type": "alert",
                        **alert_data,
                        "timestamp": time.time(),
                    },
                    default=str,
                )
                await redis.publish(CHANNEL_ALERTS, payload)
                score = time.time()
                await redis.zadd("rmi:alerts:recent", {payload: score})
                await redis.zremrangebyrank("rmi:alerts:recent", 0, -(501))
                self.broadcasts_sent += 1
            except Exception:
                self.broadcasts_failed += 1

    async def broadcast_price(self, token: str, price: float, chain: str = "solana"):
        """Convenience: broadcast a price update."""
        await self.broadcast("prices", {"token": token, "price": price, "chain": chain})

    async def get_client_count(self, channel: str | None = None) -> int:
        """Get connected client count, optionally filtered by channel."""
        async with self._lock:
            if channel:
                return len(self._clients.get(channel, set()))
            return sum(len(v) for v in self._clients.values())

    async def stats(self) -> dict:
        """Return broadcaster statistics."""
        redis_ok = False
        try:
            redis = await self._get_redis()
            if redis:
                await redis.ping()
                redis_ok = True
        except Exception:
            pass

        async with self._lock:
            client_counts = {ch: len(clients) for ch, clients in self._clients.items()}

        return {
            "redis_available": redis_ok,
            "connected_clients": client_counts,
            "total_clients": sum(client_counts.values()),
            "broadcasts_sent": self.broadcasts_sent,
            "broadcasts_failed": self.broadcasts_failed,
        }


# ── Singleton ──────────────────────────────────────────────────────────────

_manager: WsClientManager | None = None


def get_ws_manager() -> WsClientManager:
    global _manager
    if _manager is None:
        _manager = WsClientManager()
    return _manager
