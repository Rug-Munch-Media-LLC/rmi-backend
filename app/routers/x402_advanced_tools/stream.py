"""
Layer 3: SSE Real-Time Alert Stream — Server-Sent Events for security alerts.

Emits rug_pull, whale_move, price_crash events as JSON over SSE.
Reads from Redis alert queues.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import Query
from fastapi.responses import StreamingResponse

from app.core.logging import get_logger

logger = get_logger("x402.stream")


async def sse_alert_stream(
    events: str = Query(default="rug_pull,whale_move,price_crash", description="Comma-separated event types"),
    chain: str = Query(default="all"),
) -> Any:
    """Server-Sent Events stream for real-time security alerts.

    Connect with: EventSource('/api/v1/x402-tools/stream/alerts?events=rug_pull,whale_move')
    """
    event_list = [e.strip() for e in events.split(",") if e.strip()]
    chain_filter = chain.strip().lower()

    async def event_generator() -> Any:
        import redis as _redis

        r = _redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=3,
        )
        last_id = "0"

        yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'events': event_list, 'chain': chain_filter, 'timestamp': datetime.now(UTC).isoformat()})}\n\n"

        while True:
            try:
                alerts = r.lrange("x402:alerts:high_risk", 0, 9)
                for alert_json in alerts:
                    try:
                        alert = json.loads(alert_json)
                        alert_id = alert.get("timestamp", "")
                        if alert_id > last_id:
                            event_type = alert.get("type", "unknown")
                            if event_type in event_list or "all" in event_list:
                                yield f"event: {event_type}\ndata: {json.dumps(alert)}\n\n"
                            last_id = alert_id
                    except json.JSONDecodeError:
                        continue

                for evt in event_list:
                    evt_alerts = r.lrange(f"x402:alert:{evt}", 0, 4)
                    for a in evt_alerts:
                        try:
                            data = json.loads(a)
                            if data.get("timestamp", "") > last_id:
                                yield f"event: {evt}\ndata: {json.dumps(data)}\n\n"
                                last_id = data.get("timestamp", "")
                        except json.JSONDecodeError:
                            continue

                yield f": keepalive {int(time.time())}\n\n"
                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"SSE stream error: {e}")
                await asyncio.sleep(10)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


__all__ = ["sse_alert_stream"]
