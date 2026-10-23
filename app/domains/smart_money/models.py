"""Pydantic models for /api/v1/smart-money/moves.

Field contract is frozen: consumers (frontend feed, bot alerts) rely on it.
Unknown evidence is ``None`` plus a ``reasons`` entry - never a guess.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic needs it at runtime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Supported lookback windows, in hours. Query param ``window``.
WINDOWS: tuple[int, ...] = (1, 4, 24, 168)


class SmartMoneyMove(BaseModel):
    """One scored accumulation/distribution move by a wallet on a token."""

    model_config = ConfigDict(extra="forbid")

    wallet: str
    label: str | None = None
    chain: str
    token: str
    symbol: str | None = None
    action: Literal["buy", "sell"]
    amount_usd: float = Field(ge=0)
    price: float | None = None
    pnl_usd: float | None = None
    pnl_pct: float | None = None
    win_rate: float | None = Field(default=None, ge=0, le=1)
    roi_30d: float | None = None
    netflow_usd: float
    cluster_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)


class SmartMoneyMovesResponse(BaseModel):
    """Envelope for the moves feed."""

    model_config = ConfigDict(extra="forbid")

    items: list[SmartMoneyMove]
    generated_at: datetime
