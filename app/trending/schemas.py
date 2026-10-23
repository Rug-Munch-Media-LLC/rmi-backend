"""Response contract for GET /api/v1/trending.

Every numeric field is nullable and, when null, carries an entry in
``null_reasons`` explaining why the value could not be derived. No field is
ever filled with a placeholder or an inferred guess.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic resolves at runtime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TrendingItem(BaseModel):
    """One trending token."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chain: str = Field(..., min_length=1, max_length=32)
    token: str = Field(..., min_length=1, max_length=128)
    symbol: str | None = Field(None, max_length=40)
    name: str | None = Field(None, max_length=120)
    price: float | None = Field(None, ge=0)
    market_cap: float | None = Field(None, ge=0)
    liquidity: float | None = Field(None, ge=0)
    price_change_pct: float | None = None
    volume_usd: float | None = Field(None, ge=0)
    trades: int | None = Field(None, ge=0)
    buys: int | None = Field(None, ge=0)
    sells: int | None = Field(None, ge=0)
    bonding_progress: float | None = Field(None, ge=0, le=1)
    score: float = Field(..., ge=0, le=100)
    source: str = Field(..., min_length=1, max_length=40)
    last_event_at: datetime | None = None
    null_reasons: dict[str, str] = Field(default_factory=dict)


class TrendingResponse(BaseModel):
    """Envelope returned by the endpoint."""

    window_hours: int = Field(..., ge=1)
    chain: str | None = None
    items: list[TrendingItem]
    generated_at: datetime
    degraded: list[str] = Field(default_factory=list)
    source: Literal["clickhouse", "clickhouse+parquet", "empty"] = "clickhouse"
