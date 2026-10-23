"""Request/response schemas for the Polymarket attribution API."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic field types
from decimal import Decimal  # noqa: TC003 — pydantic field types
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Surface = Literal["content", "scan", "bot", "frontend"]
EventType = Literal["view", "click", "signup", "order"]


class AttributionEventIn(BaseModel):
    """Ingest payload. Raw ``wallet``/``builder_code`` are hashed, never stored."""

    model_config = ConfigDict(extra="forbid")

    partner: str = Field(min_length=1, max_length=64)
    campaign: str = Field(min_length=1, max_length=64)
    surface: Surface
    event: EventType
    market_id: str | None = Field(default=None, max_length=128)
    wallet: str | None = Field(default=None, max_length=128)
    builder_code: str | None = Field(default=None, max_length=128)
    ts: datetime | None = None
    value_usd: Decimal | None = Field(default=None, ge=0, le=1_000_000)


class AttributionEventOut(BaseModel):
    """Persisted event identity — hashes only, no raw identifiers."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    partner: str
    campaign: str
    surface: str
    event: str
    market_id: str | None
    wallet_hash: str | None
    builder_code_hash: str | None
    ts: datetime
    value_usd: str | None
    duplicate: bool = False


class Envelope(BaseModel):
    """Uniform response envelope (api-forge): data + error + request_id."""

    data: Any = None
    meta: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    request_id: str
