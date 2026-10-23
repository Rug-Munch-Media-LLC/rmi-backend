"""Response models for GET /api/v1/polymarket/opportunities.

Every ranking sub-score is nullable: an input the upstream does not expose is
reported as ``raw=None`` with a ``reason`` instead of a fabricated zero.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScoreComponent(BaseModel):
    """One weighted sub-score, with the reason when it could not be computed."""

    name: str
    weight: float = Field(..., ge=0.0, le=1.0)
    raw: float | None = Field(None, ge=0.0, le=1.0, description="Normalized 0..1 input")
    contribution: float = Field(0.0, description="weight * raw (0.0 when raw is null)")
    reason: str | None = Field(None, description="Why raw is null, or how it was derived")


class HolderEntry(BaseModel):
    """One publicly visible holder of a market outcome."""

    wallet: str
    name: str | None = None
    outcome: str | None = None
    amount: float | None = None
    share: float | None = Field(None, ge=0.0, le=1.0)


class WhaleEvidence(BaseModel):
    """Public-wallet concentration evidence from the Polymarket Data API v2."""

    available: bool
    source_url: str
    condition_id: str
    top_holders: list[HolderEntry] = Field(default_factory=list)
    holder_count: int | None = None
    top5_share: float | None = Field(None, ge=0.0, le=1.0)
    concentration_score: float | None = Field(None, ge=0.0, le=1.0)
    reason: str | None = None


class Opportunity(BaseModel):
    """One ranked, actionable-by-a-human market. Data only — never execution."""

    rank: int
    condition_id: str
    slug: str | None = None
    question: str
    url: str
    referral_url: str | None = Field(None, description="Operator-configured only; never synthesized")
    source_urls: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    prices: list[float] = Field(default_factory=list)
    yes_price: float | None = Field(None, ge=0.0, le=1.0)
    volume_usd: float = 0.0
    liquidity_usd: float = 0.0
    end_date: str | None = None
    hours_to_resolution: float | None = None
    odds_delta: float | None = None
    odds_delta_window: str | None = None
    odds_delta_reason: str | None = None
    whale_evidence: WhaleEvidence | None = None
    score: float | None = Field(None, ge=0.0, le=1.0)
    score_reason: str | None = None
    score_components: list[ScoreComponent] = Field(default_factory=list)


class OpportunitiesResponse(BaseModel):
    count: int
    window_hours: int
    min_volume_usd: float
    min_liquidity_usd: float
    weights: dict[str, float]
    generated_at: str
    degraded: bool = False
    degraded_reason: str | None = None
    dropped: dict[str, int] = Field(default_factory=dict)
    rate_limit: dict[str, Any] = Field(default_factory=dict)
    items: list[Opportunity] = Field(default_factory=list)
