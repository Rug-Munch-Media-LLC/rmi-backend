"""
Supabase Realtime Bridge — live PostgreSQL changes → Redis pub/sub → WebSocket clients.
Uses proper WebSocket protocol via 'websockets' library (httpx doesn't support WS upgrades).
"""

import asyncio
import json
import logging
import os
from datetime import UTC, datetime

import redis.asyncio as aioredis
import websockets

logger = logging.getLogger("supabase.realtime")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""

TABLE_CHANNELS = {
    "security_alerts": "rmi:ws:alerts",
    "whale_alerts": "rmi:ws:alerts",
    "intelligence_alerts": "rmi:ws:alerts",
    "rugpull_alerts": "rmi:ws:alerts",
    "scam_alerts": "rmi:ws:alerts",
    "scan_results": "rmi:ws:scans",
    "market_data": "rmi:ws:market",
    "market_global_metrics": "rmi:ws:market",
    "market_trending_tokens": "rmi:ws:market",
    "notifications": "rmi:ws:notifications",
    "system_health_log": "rmi:ws:health",
    "activity_feed": "rmi:ws:activity",
}


class SupabaseRealtimeBridge:
    def __init__(self):
        self.redis = None
        self._running = False
        self._reconnect_delay = 1

    async def start(self):
        if not SUPABASE_URL or not SUPABASE_KEY:
            logger.warning("Supabase Realtime: no URL/key")
            return
        redis_host = os.getenv("REDIS_HOST", "rmi-redis")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_pass = os.getenv("REDIS_PASSWORD", "")
        self.redis = await aioredis.from_url(
            f"redis://{redis_host}:{redis_port}",
            password=redis_pass or None,
            decode_responses=True,
        )
        self._running = True
        asyncio.create_task(self._realtime_loop())
        logger.info("Supabase Realtime bridge started — %d tables → Redis", len(TABLE_CHANNELS))

    async def stop(self):
        self._running = False
        if self.redis:
            await self.redis.close()

    async def _realtime_loop(self):
        ws_url = SUPABASE_URL.replace("https://", "wss://") + "/realtime/v1/websocket"
        params = f"?apikey={SUPABASE_KEY}"

        while self._running:
            try:
                async with websockets.connect(
                    ws_url + params,
                    ping_interval=30,
                    ping_timeout=10,
                    max_size=2**20,
                ) as ws:
                    logger.info("Supabase Realtime WebSocket connected")
                    self._reconnect_delay = 1

                    # Subscribe to all tables
                    for table, _channel in TABLE_CHANNELS.items():
                        join_msg = {
                            "topic": f"realtime:public:{table}",
                            "event": "phx_join",
                            "payload": {
                                "config": {
                                    "broadcast": {"self": True},
                                    "postgres_changes": [{"event": "*", "schema": "public", "table": table}],
                                }
                            },
                            "ref": f"join_{table}",
                        }
                        await ws.send(json.dumps(join_msg))
                        logger.debug("Subscribed to %s", table)

                    # Read messages
                    while self._running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                            await self._handle_message(raw)
                        except TimeoutError:
                            await ws.ping()
                            continue

            except Exception as e:
                logger.warning("Realtime WS disconnected: %s — reconnecting in %ds", e, self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    async def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Phoenix channels format
        topic = msg.get("topic", "")
        event = msg.get("event", "")
        payload = msg.get("payload", {})

        if not topic.startswith("realtime:public:"):
            return

        table = topic.replace("realtime:public:", "")
        channel = TABLE_CHANNELS.get(table)
        if not channel:
            return

        # Extract change data
        if event == "phx_reply":
            return  # Join acknowledgement, skip

        data = payload.get("data") or payload.get("record") or {}
        if isinstance(data, dict):
            record = data
        elif isinstance(data, list) and data:
            record = data[0] if isinstance(data[0], dict) else {}
        else:
            record = {"raw": str(data)[:500]}

        out = {
            "type": payload.get("eventType", event).lower(),
            "table": table,
            "record": record,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        if self.redis:
            await self.redis.publish(channel, json.dumps(out, default=str))


# ── HTTP polling fallback ──
async def poll_realtime_changes():
    """Poll Supabase REST API for recent changes (falls back when WebSocket unavailable)."""
    import httpx

    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    redis = await aioredis.from_url(
        f"redis://{os.getenv('REDIS_HOST', 'rmi-redis')}:{os.getenv('REDIS_PORT', '6379')}",
        password=os.getenv("REDIS_PASSWORD", "") or None,
        decode_responses=True,
    )
    last_seen = {}
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                for table, channel in [
                    ("security_alerts", "rmi:ws:alerts"),
                    ("whale_alerts", "rmi:ws:alerts"),
                    ("scan_results", "rmi:ws:scans"),
                    ("market_data", "rmi:ws:market"),
                    ("notifications", "rmi:ws:notifications"),
                ]:
                    last = last_seen.get(table, datetime.now(UTC).isoformat())
                    r = await c.get(
                        f"{SUPABASE_URL}/rest/v1/{table}",
                        params={
                            "select": "*",
                            "created_at": f"gt.{last}",
                            "order": "created_at.desc",
                            "limit": "20",
                        },
                        headers=headers,
                    )
                    if r.status_code == 200:
                        rows = r.json()
                        if rows:
                            for row in rows:
                                msg = {
                                    "type": "poll",
                                    "table": table,
                                    "record": row,
                                    "timestamp": datetime.now(UTC).isoformat(),
                                }
                                await redis.publish(channel, json.dumps(msg, default=str))
                            last_seen[table] = datetime.now(UTC).isoformat()
        except Exception as e:
            logger.warning("Realtime poll error: %s", e)
        await asyncio.sleep(2)


bridge = SupabaseRealtimeBridge()
