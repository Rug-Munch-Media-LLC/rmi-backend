"""Admin alerts webhook - /api/v1/admin/alerts/webhook.

Stub endpoint for receiving alert webhooks from external sources
(monitoring, observability platforms).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/alerts", tags=["admin"])

class AlertWebhookPayload(BaseModel):
    """Generic webhook payload from external alert sources."""

    source: str  # "prometheus" | "grafana" | "sentry" | "custom"
    severity: str  # "info" | "warning" | "critical"
    title: str
    description: str | None = None
    labels: dict[str, str] = {}

@router.post("/webhook")
async def receive_alert_webhook(payload: AlertWebhookPayload) -> dict[str, Any]:
    """Receive an alert webhook from external monitoring."""
    raise HTTPException(
        status_code=501,
        detail="Alert webhook ingestion not yet implemented - pending T08 GlitchTip wiring",
    )
