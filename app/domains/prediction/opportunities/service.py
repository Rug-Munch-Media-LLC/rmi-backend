"""Opportunity feed orchestration: Gamma discovery -> rank -> Data API evidence.

Read-only. There is no order placement, no wallet signing and no referral-code
synthesis anywhere in this path; ``referral_url`` is echoed only when the
operator sets ``POLYMARKET_OPPORTUNITIES_REFERRAL_URL``.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domains.prediction.opportunities.gamma import fetch_gamma_markets
from app.domains.prediction.opportunities.holders import fetch_holder_evidence
from app.domains.prediction.opportunities.models import OpportunitiesResponse, Opportunity, WhaleEvidence
from app.domains.prediction.opportunities.rate_limit import RateLimitTracker
from app.domains.prediction.opportunities.scoring import WEIGHTS, score_market

logger = get_logger(__name__)

FETCH_TIMEOUT_SECONDS = 8.0
CACHE_TTL_SECONDS = 30.0
MAX_PROBE_MARKETS = 10
_CACHE: dict[tuple[Any, ...], tuple[float, OpportunitiesResponse]] = {}


def _referral_url() -> str | None:
    value = (os.getenv("POLYMARKET_OPPORTUNITIES_REFERRAL_URL") or "").strip()
    return value or None


def _filter_markets(
    markets: list[dict[str, Any]],
    *,
    window_hours: int,
    min_volume_usd: float,
    min_liquidity_usd: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    kept: list[dict[str, Any]] = []
    dropped = {"no_end_date": 0, "past_end_date": 0, "outside_window": 0, "below_thresholds": 0}
    for market in markets:
        hours = market.get("hours_to_resolution")
        if hours is None:
            dropped["no_end_date"] += 1
            continue
        if hours < 0:
            dropped["past_end_date"] += 1
            continue
        if hours > window_hours:
            dropped["outside_window"] += 1
            continue
        if market["volume_usd"] < min_volume_usd or market["liquidity_usd"] < min_liquidity_usd:
            dropped["below_thresholds"] += 1
            continue
        kept.append(market)
    return kept, dropped


async def _probe_holders(
    client: httpx.AsyncClient,
    ranked: list[dict[str, Any]],
    probe_count: int,
    tracker: RateLimitTracker,
) -> dict[str, WhaleEvidence]:
    evidence: dict[str, WhaleEvidence] = {}
    for market in ranked[:probe_count]:
        evidence[market["condition_id"]] = await fetch_holder_evidence(client, market["condition_id"], tracker)
    return evidence


def _to_item(market: dict[str, Any], whale: WhaleEvidence | None, rank: int) -> Opportunity:
    score, components = score_market(market, whale)
    source_urls = [market["gamma_url"], market["market_url"]]
    if whale is not None:
        source_urls.append(whale.source_url)
    return Opportunity(
        rank=rank,
        condition_id=market["condition_id"],
        slug=market["slug"],
        question=market["question"],
        url=market["market_url"],
        referral_url=_referral_url(),
        source_urls=[u for u in source_urls if u],
        outcomes=market["outcomes"],
        prices=market["prices"],
        yes_price=market["yes_price"],
        volume_usd=round(market["volume_usd"], 2),
        liquidity_usd=round(market["liquidity_usd"], 2),
        end_date=market["end_date"],
        hours_to_resolution=market["hours_to_resolution"],
        odds_delta=market["odds_delta"],
        odds_delta_window=market["odds_delta_window"],
        odds_delta_reason=market["odds_delta_reason"],
        whale_evidence=whale,
        score=score,
        score_reason=None,
        score_components=components,
    )


async def build_opportunities(
    *,
    limit: int = 25,
    window_hours: int = 720,
    min_volume_usd: float = 10_000.0,
    min_liquidity_usd: float = 1_000.0,
    probe_holders: int = 5,
    client: httpx.AsyncClient | None = None,
    use_cache: bool = True,
) -> OpportunitiesResponse:
    """Build the ranked feed. Upstream failure degrades; it never raises."""
    probe_count = max(0, min(probe_holders, MAX_PROBE_MARKETS))
    cache_key = (limit, window_hours, min_volume_usd, min_liquidity_usd, probe_count)
    if use_cache and cache_key in _CACHE:
        cached_at, cached = _CACHE[cache_key]
        if time.monotonic() - cached_at < CACHE_TTL_SECONDS:
            return cached

    tracker = RateLimitTracker()
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS)
    try:
        markets = await fetch_gamma_markets(
            http_client,
            window_hours=window_hours,
            min_volume_usd=min_volume_usd,
            min_liquidity_usd=min_liquidity_usd,
            tracker=tracker,
        )
        kept, dropped = _filter_markets(
            markets,
            window_hours=window_hours,
            min_volume_usd=min_volume_usd,
            min_liquidity_usd=min_liquidity_usd,
        )
        pre_scored = sorted(((score_market(m, None)[0], m) for m in kept), key=lambda pair: pair[0], reverse=True)
        ranked = [market for _, market in pre_scored]
        evidence = await _probe_holders(http_client, ranked, probe_count, tracker)
    finally:
        if owns_client:
            await http_client.aclose()

    final = sorted(
        ((score_market(m, evidence.get(m["condition_id"]))[0], m) for m in ranked),
        key=lambda pair: pair[0],
        reverse=True,
    )[:limit]
    items = [_to_item(market, evidence.get(market["condition_id"]), idx) for idx, (_, market) in enumerate(final, 1)]
    degraded = not markets
    response = OpportunitiesResponse(
        count=len(items),
        window_hours=window_hours,
        min_volume_usd=min_volume_usd,
        min_liquidity_usd=min_liquidity_usd,
        weights=WEIGHTS,
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        degraded=degraded,
        degraded_reason="gamma_unavailable_or_empty" if degraded else None,
        dropped=dropped,
        rate_limit=tracker.summary(),
        items=items,
    )
    if not degraded and use_cache:
        _CACHE[cache_key] = (time.monotonic(), response)
    logger.info(
        "polymarket.opportunities.built",
        extra={"fetched": len(markets), "kept": len(kept), "returned": len(items), "degraded": degraded},
    )
    return response
