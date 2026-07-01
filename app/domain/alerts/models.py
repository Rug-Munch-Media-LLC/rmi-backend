"""Pydantic v2 models for the alerts domain.

No FastAPI imports. Pure data shapes.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AlertType(StrEnum):
    """Valid alert trigger types. Matches the legacy alert_types enum."""

    LIQUIDITY_REMOVE = "liquidity_remove"
    MINT = "mint"
    BLACKLIST = "blacklist"
    HONEYPOT = "honeypot"
    RUG_PULL = "rug_pull"
    WHALE_MOVEMENT = "whale_movement"
    DEPLOYER_SELL = "deployer_sell"

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


class AlertSubscription(BaseModel):
    """A token alert subscription record (what we store in Redis)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(..., description="Unique subscription id, e.g. alert:1700000000")
    token_address: str = Field(..., min_length=1, max_length=256)
    alert_types: list[AlertType] = Field(default_factory=lambda: [AlertType.LIQUIDITY_REMOVE, AlertType.MINT, AlertType.BLACKLIST])
    webhook_url: str | None = Field(default=None, max_length=2048)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = True
    owner_id: str | None = Field(default=None, description="User id of subscription owner, None = anonymous")

    @field_validator("alert_types")
    @classmethod
    def _at_least_one_type(cls, v: list[AlertType]) -> list[AlertType]:
        if not v:
            raise ValueError("at least one alert_type required")
        return v


class CreateAlertRequest(BaseModel):
    """What the API accepts to create a subscription."""

    model_config = ConfigDict(str_strip_whitespace=True)

    token_address: str = Field(..., min_length=1, max_length=256)
    alert_types: list[AlertType] | None = None
    webhook_url: str | None = Field(default=None, max_length=2048)


class AlertEvent(BaseModel):
    """A fired alert, ready to broadcast to subscribers + WebSocket clients."""

    model_config = ConfigDict(use_enum_values=True)

    subscription_id: str
    token_address: str
    event_type: AlertType
    payload: dict[str, Any] = Field(default_factory=dict)
    fired_at: datetime = Field(default_factory=datetime.utcnow)
    severity: str = Field(default="info", description="info | warning | critical")
