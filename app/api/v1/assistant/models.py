"""Request/response models for the page-context API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

PageName = Literal["rugcharts", "rugmaps", "scanner", "trades"]

_CHAIN = r"^(solana|ethereum|bsc|polygon|arbitrum|base|optimism|avalanche)$"
_TIMEFRAME = r"^(1m|5m|15m|30m|1h|4h|1d|1w)$"


class PageParams(BaseModel):
    """Query parameters shared by every page-context builder."""

    token: str = Field(min_length=20, max_length=90, description="Token address")
    chain: str = Field("solana", pattern=_CHAIN)
    timeframe: str = Field("1h", pattern=_TIMEFRAME, description="RugCharts candle timeframe")
    pair: str | None = Field(None, min_length=10, max_length=90, description="Pair address override")
    limit: int = Field(50, ge=5, le=200)
    cluster: str | None = Field(None, max_length=128, description="Selected cluster id")


class Section(BaseModel):
    """One page section — data, or null with the reason it is absent."""

    source: str
    data: dict[str, Any] | None = None
    reason: str | None = None


class Citation(BaseModel):
    """A grounded RAG citation (RugBrain)."""

    collection: str = ""
    doc_id: str = ""
    score: float = 0.0
    snippet: str = ""


class PageContextResponse(BaseModel):
    """Page context envelope consumed by the assistant panel."""

    page: PageName
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["ok", "partial", "unavailable"] = "ok"
    subject: dict[str, Any] = Field(default_factory=dict)
    sections: dict[str, Section] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
