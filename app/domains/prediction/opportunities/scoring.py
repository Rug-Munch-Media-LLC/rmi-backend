"""Ranking model for Polymarket opportunities — documented, deterministic weights.

WEIGHTS (sum = 1.00, asserted at import)

    volume              0.28  log10(1 + volume_usd) / log10(1 + 10M), capped 1
    liquidity           0.20  log10(1 + liquidity_usd) / log10(1 + 1M), capped 1
    odds_movement       0.18  abs(odds_delta) / 0.25, capped 1
    near_resolution     0.14  1 - hours/168 for the next 7 days, else 0
    catalyst            0.12  best keyword tier in question + tags + event titles
    whale_concentration 0.08  Data API v2 top-5 holder share, capped 1

Rationale: volume and liquidity decide whether a human can actually act; odds
movement is the only free directional signal; near-resolution carries the time
premium; catalyst captures event-driven attention; whale concentration is
weighted lowest on purpose — public holder rows lag real positioning and are
sybil-friendly, so they are evidence, not a thesis.

A sub-score whose upstream input is missing is reported as ``raw=None`` with a
reason and contributes 0.0. The market keeps a score, and the response carries
the reasons, so a consumer can discount it rather than trust a silent zero.
"""

from __future__ import annotations

import math
from typing import Any

from app.domains.prediction.opportunities.models import ScoreComponent, WhaleEvidence

WEIGHTS: dict[str, float] = {
    "volume": 0.28,
    "liquidity": 0.20,
    "odds_movement": 0.18,
    "near_resolution": 0.14,
    "catalyst": 0.12,
    "whale_concentration": 0.08,
}

VOLUME_CAP_USD = 10_000_000.0
LIQUIDITY_CAP_USD = 1_000_000.0
ODDS_DELTA_CAP = 0.25
NEAR_RESOLUTION_HORIZON_HOURS = 168.0

CATALYST_TIERS: tuple[tuple[float, tuple[str, ...]], ...] = (
    (1.00, ("sec", "doj", "indictment", "arrest", "hack", "exploit", "bankrupt", "etf approval", "emergency")),
    (0.70, ("election", "etf", "rate cut", "rate hike", "fed ", "tariff", "lawsuit", "listing", "mainnet")),
    (0.40, ("airdrop", "partnership", "launch", "upgrade", "halving", "acquisition", "merger")),
)

assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "WEIGHTS must sum to 1.0"


def _log_norm(value: float, cap: float) -> float:
    if value <= 0:
        return 0.0
    return min(1.0, math.log10(1.0 + value) / math.log10(1.0 + cap))


def catalyst_tier(market: dict[str, Any]) -> tuple[float, str | None]:
    """Best matching catalyst tier for the market text, or (0.0, None)."""
    parts: list[str] = [str(market.get("question") or "")]
    for tag in market.get("tags") or []:
        parts.append(str(tag.get("label") if isinstance(tag, dict) else tag))
    for event in market.get("events") or []:
        if isinstance(event, dict):
            parts.append(str(event.get("title") or ""))
    haystack = " ".join(parts).lower()
    for tier, keywords in CATALYST_TIERS:
        hit = next((kw for kw in keywords if kw in haystack), None)
        if hit:
            return tier, f"matched_catalyst_keyword:{hit.strip()}"
    return 0.0, None


def score_market(market: dict[str, Any], whale: WhaleEvidence | None) -> tuple[float, list[ScoreComponent]]:
    """Return (score in 0..1, per-component breakdown with null reasons)."""
    components: list[ScoreComponent] = []

    def add(name: str, raw: float | None, reason: str | None) -> None:
        weight = WEIGHTS[name]
        contribution = round(weight * raw, 6) if raw is not None else 0.0
        components.append(ScoreComponent(name=name, weight=weight, raw=raw, contribution=contribution, reason=reason))

    volume = float(market.get("volume_usd") or 0.0)
    add(
        "volume",
        round(_log_norm(volume, VOLUME_CAP_USD), 6),
        f"log10_normalized_cap_{int(VOLUME_CAP_USD)}usd" if volume > 0 else "gamma_volume_zero_or_missing",
    )

    liquidity = float(market.get("liquidity_usd") or 0.0)
    add(
        "liquidity",
        round(_log_norm(liquidity, LIQUIDITY_CAP_USD), 6),
        f"log10_normalized_cap_{int(LIQUIDITY_CAP_USD)}usd" if liquidity > 0 else "gamma_liquidity_zero_or_missing",
    )

    delta = market.get("odds_delta")
    if delta is None:
        add("odds_movement", None, market.get("odds_delta_reason") or "odds_delta_unavailable")
    else:
        add(
            "odds_movement",
            round(min(1.0, abs(float(delta)) / ODDS_DELTA_CAP), 6),
            f"abs_delta_over_{ODDS_DELTA_CAP}_window_{market.get('odds_delta_window')}",
        )

    hours = market.get("hours_to_resolution")
    if hours is None:
        add("near_resolution", None, "end_date_unparsable")
    elif hours < 0:
        add("near_resolution", None, "market_past_end_date")
    else:
        add(
            "near_resolution",
            round(max(0.0, 1.0 - hours / NEAR_RESOLUTION_HORIZON_HOURS), 6),
            f"linear_decay_over_{int(NEAR_RESOLUTION_HORIZON_HOURS)}h",
        )

    tier, tier_reason = catalyst_tier(market)
    add("catalyst", tier, tier_reason or "no_catalyst_keyword_matched")

    if whale is None:
        add("whale_concentration", None, "holder_probe_not_selected")
    elif not whale.available or whale.concentration_score is None:
        add("whale_concentration", None, whale.reason or "data_api_unavailable")
    else:
        add("whale_concentration", round(whale.concentration_score, 6), "data_api_top5_holder_share")

    total = round(sum(c.contribution for c in components), 6)
    return min(1.0, total), components
