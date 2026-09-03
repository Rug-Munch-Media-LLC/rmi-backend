"""Admin alerts webhook - /api/v1/admin/alerts/webhook.

Receives Prometheus Alertmanager webhook payloads and routes them to
Redis queues for downstream processing:

- Infrastructure alerts -> ``rmi:alerts:system``
- Security alerts       -> ``rmi:alerts:security``

Fail-open: if Redis is unavailable the request still returns 200 so the
alert source does not retry/backlog.
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.logging import get_logger

try:
    import redis.asyncio as redis
except ImportError:  # pragma: no cover - guarded import
    redis = None  # type: ignore[assignment]

logger = get_logger(__name__)

router = APIRouter(prefix="/alerts", tags=["admin"])

# ── Redis queue names ────────────────────────────────────────────────
SYSTEM_QUEUE = "rmi:alerts:system"
SECURITY_QUEUE = "rmi:alerts:security"

# Infrastructure alert names routed to the system queue.
INFRA_ALERT_NAMES = {
    "ClickHouseDown",
    "RedisDown",
    "PostgresDown",
    "ApiLatencyCritical",
    "ApiHighErrorRate",
    "PrometheusTargetMissing",
}

# Security alert names are matched by substring (case-insensitive).
SECURITY_KEYWORDS = ("exploit", "hack", "rug")


class AlertmanagerAlert(BaseModel):
    """A single alert from the Prometheus Alertmanager webhook."""

    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: str | None = Field(default=None, alias="startsAt")
    ends_at: str | None = Field(default=None, alias="endsAt")
    generator_url: str | None = Field(default=None, alias="generatorURL")
    fingerprint: str | None = None


class AlertmanagerWebhookPayload(BaseModel):
    """Prometheus Alertmanager webhook payload."""

    status: str = "firing"
    alerts: list[AlertmanagerAlert] = Field(default_factory=list[AlertmanagerAlert])
    group_labels: dict[str, str] = Field(default_factory=dict, alias="groupLabels")
    common_labels: dict[str, str] = Field(default_factory=dict, alias="commonLabels")
    common_annotations: dict[str, str] = Field(default_factory=dict, alias="commonAnnotations")
    external_url: str | None = Field(default=None, alias="externalURL")
    version: str | None = None
    receiver: str | None = None

    model_config = {"populate_by_name": True}


def _classify(alert: AlertmanagerAlert) -> str:
    """Return the queue name an alert should be routed to."""
    name = alert.labels.get("alertname", "")
    if name in INFRA_ALERT_NAMES:
        return SYSTEM_QUEUE
    lowered = name.lower()
    if any(kw in lowered for kw in SECURITY_KEYWORDS):
        return SECURITY_QUEUE
    # Default: infrastructure/system bucket for anything not security-flagged.
    return SYSTEM_QUEUE


async def _get_redis() -> Any | None:
    """Lazy Redis client (fail-open: returns None when unavailable)."""
    if redis is None:
        return None
    try:
        host = os.getenv("REDIS_HOST", "127.0.0.1")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD") or None
        db = int(os.getenv("REDIS_DB", "0"))
        return redis.Redis(host=host, port=port, password=password, db=db, decode_responses=True)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[alerts] Redis init failed: %s", exc)
        return None


async def _push(r: Any, queue: str, alert: AlertmanagerAlert) -> None:
    """Push a single alert onto a Redis list queue."""
    await r.rpush(queue, json.dumps(alert.model_dump(by_alias=True)))


@router.post("/webhook")
async def receive_alert_webhook(payload: AlertmanagerWebhookPayload) -> dict[str, Any]:
    """Receive an Alertmanager webhook and route alerts to Redis queues."""
    received = len(payload.alerts)
    routed = "system"

    r = await _get_redis()
    if r is None:
        logger.warning("[alerts] Redis unavailable; dropping %d alert(s) (fail-open)", received)
        return {"received": received, "routed": routed}

    try:
        for alert in payload.alerts:
            queue = _classify(alert)
            await _push(r, queue, alert)
            if queue == SECURITY_QUEUE:
                routed = "security"
    except Exception as exc:  # pragma: no cover - defensive
        # Fail-open: never propagate a Redis failure to the alert source.
        logger.warning("[alerts] Redis push failed; dropping %d alert(s): %s", received, exc)
        return {"received": received, "routed": routed}

    logger.info(
        "[alerts] routed %d alert(s) to %s",
        received,
        "security" if routed == "security" else "system",
    )
    return {"received": received, "routed": routed}
