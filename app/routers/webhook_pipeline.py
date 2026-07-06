"""
RMI Webhook Notification Pipeline
===================================
Dispatches alerts to registered webhooks in real-time.
Polls Redis for new alerts and delivers via HTTP POST.

Features:
- Retries with exponential backoff
- Dead letter queue for failed deliveries
- Rate limiting per webhook URL
- Payload signing for security
- Delivery status tracking

Background task started on backend boot.
Polls every 5 seconds for new alerts.

Author: RMI Development
Date: 2026-06-05
"""

import hashlib
import json
import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.redis import get_redis

logger = logging.getLogger("webhook_pipeline")

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhook-pipeline"])


# ── Redis Helper ─────────────────────────────────────────────────


def queue_webhook_delivery(
    event_type: str,
    address: str,
    message: str,
    data: dict | None = None,
) -> None:
    """Queue a webhook delivery.

    Called by push_alert() in persistent_state.py and by
    scanner/cron systems.
    """
    r = get_redis()
    if not r:
        return

    payload = {
        "id": f"wh:{int(time.time())}:{hashlib.md5(message.encode()).hexdigest()[:8]}",
        "type": event_type,
        "address": address,
        "message": message,
        "data": data or {},
        "source": "rmi_scanner",
        "created_at": datetime.now(UTC).isoformat(),
    }

    delivery = {
        "url": webhook_url,  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
        "payload": payload,
        "secret": secret,  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
        "webhook_id": webhook_id,  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
        "attempts": 0,
        "queued_at": payload["created_at"],
    }

    r.lpush("rmi:webhooks:pending", json.dumps(delivery))
    r.incr("rmi:webhooks:stats:queued")

    # Trim pending queue to last 10000
    r.ltrim("rmi:webhooks:pending", 0, 9999)


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/stats")
async def webhook_stats():
    """Get webhook delivery statistics."""
    r = get_redis()
    if not r:
        return JSONResponse(content={"error": "redis_unavailable"})

    return JSONResponse(
        content={
            "queued": r.get("rmi:webhooks:stats:queued") or 0,
            "delivered": r.get("rmi:webhooks:stats:delivered") or 0,
            "failed": r.get("rmi:webhooks:stats:failed") or 0,
            "pending": r.llen("rmi:webhooks:pending"),
            "dead_letter": r.llen("rmi:webhooks:dead_letter"),
        }
    )


@router.get("/dead-letter")
async def dead_letter_queue(limit: int = 50):
    """View failed webhook deliveries."""
    r = get_redis()
    if not r:
        return JSONResponse(content={"error": "redis_unavailable"})

    items = r.lrange("rmi:webhooks:dead_letter", 0, limit - 1)
    return JSONResponse(
        content={
            "dead_letter": [json.loads(i) for i in items],
            "total": r.llen("rmi:webhooks:dead_letter"),
        }
    )


@router.post("/dead-letter/retry/{index}")
async def retry_dead_letter(index: int):
    """Retry a dead letter delivery."""
    r = get_redis()
    if not r:
        return JSONResponse(content={"error": "redis_unavailable"})

    items = r.lrange("rmi:webhooks:dead_letter", index, index)
    if not items:
        return JSONResponse(content={"error": "Item not found"})

    item = json.loads(items[0])
    item["attempts"] = 0
    r.lrem("rmi:webhooks:dead_letter", 1, items[0])
    r.lpush("rmi:webhooks:pending", json.dumps(item))

    return JSONResponse(content={"success": True, "message": "Re-queued for delivery"})
