"""Pydantic v2 models for the webhook system.

No FastAPI imports. Pure data shapes matching the webhook domain.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebhookEventType(StrEnum):
    """The seven webhook event types — all sourced from free data."""

    TOKEN_DEPLOYED = "token.deployed"  # noqa: S105 — config default
    TOKEN_FIRST_SWAP = "token.first_swap"  # noqa: S105 — config default
    TOKEN_PRICE_CHANGE = "token.price_change"  # noqa: S105 — config default
    TOKEN_WHALE_MOVE = "token.whale_move"  # noqa: S105 — config default
    TOKEN_LIQUIDITY_CHANGE = "token.liquidity_change"  # noqa: S105 — config default
    WALLET_ACTIVITY = "wallet.activity"
    CHAIN_BLOCK = "chain.block"

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


class WebhookFilters(BaseModel):
    """Optional user-configurable filters per event type."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # Token/chain filters
    chains: list[str] = Field(
        default_factory=list,
        description="Filter by chain IDs (e.g. ['ethereum', 'solana']). Empty = all chains.",
    )
    token_addresses: list[str] = Field(
        default_factory=list,
        description="Filter by specific token addresses. Empty = all tokens.",
    )
    min_value_usd: float | None = Field(default=None, ge=0, description="Minimum USD value for whale/liquidity events.")
    price_change_pct: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Minimum % price change for price_change events.",
    )
    wallet_addresses: list[str] = Field(
        default_factory=list,
        description="Watch specific wallets for wallet.activity events. Empty = no wallet filter.",
    )


class WebhookSubscription(BaseModel):
    """A webhook subscription — stored in Redis."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(..., description="Unique ID, e.g. whk:1712000000")
    url: str = Field(..., min_length=1, max_length=2048, description="Webhook callback URL")
    event_types: list[WebhookEventType] = Field(..., min_length=1, description="Event types to subscribe to")
    filters: WebhookFilters = Field(default_factory=WebhookFilters)
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    owner_id: str | None = Field(default=None, max_length=256)
    last_triggered_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("webhook_url must start with http:// or https://")
        return v

    @field_validator("event_types")
    @classmethod
    def _at_least_one(cls, v: list[WebhookEventType]) -> list[WebhookEventType]:
        if not v:
            raise ValueError("at least one event_type required")
        return v


class WebhookEvent(BaseModel):
    """A detected event ready to be dispatched."""

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default="", description="Unique event ID, e.g. evt:1712000001")
    subscription_id: str = Field(default="", description="Owning subscription ID")
    event_type: WebhookEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    triggered_at: datetime = Field(default_factory=datetime.utcnow)
    chain: str = Field(default="", description="Chain where event occurred")


class WebhookDelivery(BaseModel):
    """Record of a webhook delivery attempt."""

    model_config = ConfigDict(use_enum_values=True)

    event_id: str
    subscription_id: str
    url: str
    status: str = Field(default="pending", pattern=r"^(pending|delivered|failed)$")
    attempt: int = 0
    max_attempts: int = 3
    response_code: int | None = Field(default=None, ge=100, lt=600)
    error: str | None = None
    last_attempt_at: datetime | None = None


class CreateWebhookRequest(BaseModel):
    """API request to create a webhook subscription."""

    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(..., min_length=1, max_length=2048)
    event_types: list[WebhookEventType] = Field(..., min_length=1)
    filters: WebhookFilters | None = None


class WebhookEventTypeInfo(BaseModel):
    """Info about an available event type — returned by GET /events."""

    event_type: WebhookEventType
    description: str
    data_source: str
    is_free: bool = True
