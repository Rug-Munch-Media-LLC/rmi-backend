"""
Alerts Router - Token alert subscriptions
"""

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["alerts"])


class AlertRequest(BaseModel):
    token_address: str
    alert_types: list[str] = Field(default=["liquidity_remove", "mint", "blacklist"])
    webhook_url: str | None = None


from app.auth import get_redis, require_auth  # noqa: E402


def _get_redis_sync():
    """Get Redis instance synchronously (get_redis returns redis.Redis, not async)."""
    return get_redis()


@router.post("/alerts/subscribe")
async def subscribe_alert(req: AlertRequest, user: dict[str, Any] = Depends(require_auth)):
    alert_id = f"alert:{datetime.utcnow().timestamp():.0f}"
    alert_data = {
        "id": alert_id,
        "token_address": req.token_address,
        "types": req.alert_types,
        "webhook_url": req.webhook_url,
        "created_at": datetime.utcnow().isoformat(),
        "active": True,
    }

    # Redis fallback (RMI doesn't have full DB client)
    r = _get_redis_sync()
    r.hset("rmi:alerts", alert_id, json.dumps(alert_data))
    return alert_data


@router.get("/alerts")
async def list_alerts():
    r = _get_redis_sync()
    alerts_raw = r.hgetall("rmi:alerts") or {}
    alerts = [json.loads(v) for v in alerts_raw.values()]
    return {"alerts": alerts, "total": len(alerts)}
