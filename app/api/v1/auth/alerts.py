"""Auth alerts router - /api/v1/alerts/*.

Stub implementation for the alerts domain. Real implementations
will wire up to user-configured alert rules and notification channels
(email, Telegram, webhook). For now, returns 501 Not Implemented
for actual alert operations, with version metadata.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertRule(BaseModel):
    """Schema for an alert rule (creation/edit)."""

    name: str
    subject_type: str  # "token" | "wallet" | "deployer"
    subject_id: str
    trigger: str  # "risk_score_above" | "deployer_rug" | "news_mention"
    threshold: float | None = None
    channels: list[str] = []  # ["email", "telegram", "webhook"]


class AlertList(BaseModel):
    """Response for GET /api/v1/alerts."""

    count: int
    items: list[dict[str, Any]] = []


@router.get("", response_model=AlertList)
async def list_alerts() -> AlertList:
    """List all configured alert rules for the authenticated user.

    TODO: wire up to Postgres once auth context is established.
    Returns empty list as a stub so the factory can mount successfully.
    """
    return AlertList(count=0, items=[])


@router.post("", status_code=501)
async def create_alert(rule: AlertRule) -> dict[str, str]:
    """Create a new alert rule.

    Returns 501 until alert persistence is wired up. Stub so the
    factory mounts this route without crashing.
    """
    raise HTTPException(
        status_code=501,
        detail="Alert persistence not yet implemented - coming in v5.1",
    )


@router.delete("/{rule_id}", status_code=501)
async def delete_alert(rule_id: str) -> dict[str, str]:
    """Delete an alert rule by ID."""
    raise HTTPException(
        status_code=501,
        detail="Alert persistence not yet implemented - coming in v5.1",
    )
