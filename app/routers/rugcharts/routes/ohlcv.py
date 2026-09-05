"""OHLCV candle data + AI-assisted technical analysis overlay."""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Query

from app.core.data_fusion import envelope, fusion
from app.core.logging import get_logger

from ..helpers import (
    _compute_volume_authenticity,
    _fetch_ohlcv_clickhouse,
    _fetch_ohlcv_gecko,
    _fetch_pair_info,
)
from ..ta import compute_ta_snapshot

logger = get_logger("rugcharts_router")

router = APIRouter(tags=["rugcharts"])


@router.get("/ohlcv/{chain}/{pair_address}")
async def rugcharts_ohlcv(
    chain: str,
    pair_address: str,
    timeframe: str = Query("1h", description="Candle timeframe"),
    limit: int = Query(100, description="Number of candles"),
) -> Any:
    """Get OHLCV candle data for a specific pair.

    Tries the chain-indexer ClickHouse materialized view first (freshest
    on-chain data, own-data), falls back to GeckoTerminal when unavailable
    or for sub-hourly timeframes. The response's ``source`` field reports
    which backend actually served the candles.
    """
    now = datetime.datetime.now(datetime.UTC)
    candles = await _fetch_ohlcv_clickhouse(pair_address, chain, timeframe, limit)
    candle_source = "clickhouse"
    if not candles:
        candles = await _fetch_ohlcv_gecko(pair_address, chain, timeframe, limit)
        candle_source = "geckoterminal" if candles else "none"
    pair_info = await _fetch_pair_info(pair_address, chain)
    vol_auth = _compute_volume_authenticity(pair_info, candles)

    summary = {}
    if candles:
        prices = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]
        summary = {
            "current_price": prices[-1],
            "price_change_pct": round(((prices[-1] - prices[0]) / prices[0] * 100), 2) if prices[0] > 0 else 0,
            "high": max(c["high"] for c in candles),
            "low": min(c["low"] for c in candles),
            "volume": sum(volumes),
            "candle_count": len(candles),
        }

    # ── Fuse price data from available sources ──
    gecko_price = {
        "source": "coingecko",
        "price_usd": pair_info.get("base_token_price_usd", 0),
        "freshness": now.isoformat(),
        "confidence": 0.6 if pair_info.get("base_token_price_usd") else 0.0,
    }
    candle_price = (
        {
            "source": f"{candle_source}_ohlcv",
            "price_usd": prices[-1],
            "freshness": now.isoformat(),
            "confidence": 0.55,
        }
        if candles and summary.get("current_price")
        else None
    )

    price_sources = [gecko_price]
    if candle_price:
        price_sources.append(candle_price)

    fused_price = await fusion.fuse_price_data(*price_sources)

    fusion_result = {
        "price_confidence": fused_price.get("confidence", 0),
        "price_usd_fused": fused_price.get("price_usd"),
        "price_spread_pct": fused_price.get("spread_pct"),
        "price_sources_used": fused_price.get("sources_used", 0),
        "outlier_sources": fused_price.get("outlier_sources", []),
        "summary": fused_price.get("summary", ""),
    }

    return envelope(
        success=True,
        data={
            "candles": candles,
            "summary": summary,
            "pair_info": pair_info,
            "authenticity": vol_auth,
            "fusion": fusion_result,
            "timeframe": timeframe,
            "chain": chain,
            # Honest provenance — which backend actually served these candles.
            "source": candle_source,
            # Back-compat flag (kept for existing consumers).
            "fallback_used": not bool(candles),
        },
        source=f"{candle_source}+coingecko" if candles else "none",
        confidence=fused_price.get("confidence", 0.3),
    )


# ── AI-assisted technical analysis (AITA overlay) ────────────────────────


def _pivot_levels(
    candles: list[dict[str, Any]], window: int = 3, max_levels: int = 3
) -> tuple[list[float], list[float]]:
    """Swing-pivot support/resistance levels from local extrema."""
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    resistance: list[float] = []
    support: list[float] = []
    n = len(candles)
    for i in range(window, n - window):
        if highs[i] == max(highs[i - window : i + window + 1]):
            resistance.append(highs[i])
        if lows[i] == min(lows[i - window : i + window + 1]):
            support.append(lows[i])

    # Cluster nearby levels (within 1.5% of each other) and keep strongest.
    def _cluster(vals: list[float]) -> list[float]:
        vals = sorted(vals, reverse=True)
        out: list[float] = []
        for v in vals:
            if not out or abs(v - out[-1]) / out[-1] > 0.015:
                out.append(round(v, 10))
        return out[:max_levels]

    return _cluster(support), _cluster(resistance)


def _detect_pattern(candles: list[dict[str, Any]]) -> tuple[str, float, str]:
    """Heuristic chart-pattern detection over the recent candle window.

    Returns (pattern, confidence, reasoning). Pure and honest: when no
    recognizable structure exists we say so instead of forcing a label.
    """
    n = len(candles)
    if n < 30:
        return ("none", 0.0, "insufficient candle history for pattern detection")

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    start = n - 30
    peak_idx = [i - start for i in range(start + 2, n - 2) if highs[i] == max(highs[i - 2 : i + 3])]
    trough_idx = [i - start for i in range(start + 2, n - 2) if lows[i] == min(lows[i - 2 : i + 3])]

    tol = 0.02
    # Double top: two peaks within tol with a >=2% trough between them.
    if len(peak_idx) >= 2:
        p1, p2 = peak_idx[-2], peak_idx[-1]
        if p2 - p1 >= 4 and abs(highs[-30 + p1] - highs[-30 + p2]) / highs[-30 + p2] <= tol:
            trough = min(lows[-30 + p1 : -30 + p2])
            if (highs[-30 + p1] - trough) / highs[-30 + p1] >= 0.02:
                return (
                    "double_top",
                    72,
                    "Two peaks at similar price with a trough between — classic distribution shape",
                )
    # Double bottom: mirror of double top.
    if len(trough_idx) >= 2:
        t1, t2 = trough_idx[-2], trough_idx[-1]
        if t2 - t1 >= 4 and abs(lows[-30 + t1] - lows[-30 + t2]) / lows[-30 + t2] <= tol:
            peak = max(highs[-30 + t1 : -30 + t2])
            if (peak - lows[-30 + t1]) / lows[-30 + t1] >= 0.02:
                return ("double_bottom", 72, "Two lows at similar price with a rebound between — accumulation shape")
    # Ascending triangle: flat-ish highs, rising lows.
    highs_flat = abs(max(highs[-15:]) - max(highs[-30:-15])) / max(highs[-30:-15]) <= tol
    lows_rising = min(lows[-15:]) > min(lows[-30:-15]) * 1.01
    if highs_flat and lows_rising:
        return ("ascending_triangle", 65, "Horizontal resistance with rising lows — bullish compression")
    # Descending triangle: flat-ish lows, falling highs.
    if (
        abs(min(lows[-15:]) - min(lows[-30:-15])) / min(lows[-30:-15]) <= tol
        and max(highs[-15:]) < max(highs[-30:-15]) * 0.99
    ):
        return ("descending_triangle", 65, "Horizontal support with falling highs — bearish compression")
    return ("none", 0.0, "No high-confidence pattern in the recent window")


@router.get("/ai-ta/{chain}/{address}")
async def rugcharts_ai_ta(
    chain: str,
    address: str,
    timeframe: str = Query("1h", description="Candle timeframe"),
) -> Any:
    """AI-assisted technical analysis for the RugCharts AITA overlay.

    Deterministic TA (shared ta.py snapshot) + swing-pivot S/R levels +
    heuristic pattern detection, fused into a single trend call with a
    confidence score and human-readable reasoning. Honest labeling: this
    is rule-based analysis, not an LLM — the response says so.
    """
    candles = await _fetch_ohlcv_gecko(address, chain, timeframe, 100)
    if not candles:
        candles = await _fetch_ohlcv_clickhouse(address, chain, timeframe, 100)
    snapshot = compute_ta_snapshot(candles)
    if not snapshot.get("available"):
        return envelope(
            success=True,
            data={
                "chain": chain,
                "address": address,
                "available": False,
                "reason": snapshot.get("reason", "insufficient_ohlcv"),
                "trend": None,
                "pattern": None,
                "support_levels": [],
                "resistance_levels": [],
                "confidence": 0,
                "reasoning": "Not enough candle data for AI-TA on this timeframe.",
            },
            source="none",
            confidence=0.0,
        )

    support_levels, resistance_levels = _pivot_levels(candles)
    pattern, pattern_conf, pattern_reason = _detect_pattern(candles)

    trend = snapshot.get("trend", "NEUTRAL")
    rsi = snapshot.get("rsi_14") or 50
    price_vs_sma = snapshot.get("price_vs_sma20", "BELOW")
    macd_hist = snapshot.get("macd_hist")

    # Confidence: evidence count — each agreeing indicator adds weight.
    bull_votes = 0
    bear_votes = 0
    if trend == "BULLISH":
        bull_votes += 1
    else:
        bear_votes += 1
    if price_vs_sma == "ABOVE":
        bull_votes += 1
    else:
        bear_votes += 1
    if rsi > 55:
        bull_votes += 1
    elif rsi < 45:
        bear_votes += 1
    if (macd_hist or 0) > 0:
        bull_votes += 1
    elif (macd_hist or 0) < 0:
        bear_votes += 1
    if pattern == "ascending_triangle" or pattern == "double_bottom":
        bull_votes += 1
    elif pattern == "descending_triangle" or pattern == "double_top":
        bear_votes += 1

    total_votes = bull_votes + bear_votes
    confidence = round(max(bull_votes, bear_votes) / total_votes * 100) if total_votes else 50

    reasons: list[str] = []
    reasons.append(f"SMA7 {'above' if trend == 'BULLISH' else 'below'} SMA20 ({trend.lower()} structure)")
    reasons.append(f"Price trading {price_vs_sma.lower()} SMA20")
    reasons.append(
        f"RSI(14) at {rsi:.0f} — {'bullish momentum' if rsi > 55 else 'bearish momentum' if rsi < 45 else 'neutral momentum'}"
    )
    if macd_hist is not None:
        reasons.append(f"MACD histogram {'positive' if macd_hist > 0 else 'negative'}")
    if pattern != "none":
        reasons.append(f"Pattern: {pattern.replace('_', ' ')} ({pattern_reason})")

    return envelope(
        success=True,
        data={
            "chain": chain,
            "address": address,
            "timeframe": timeframe,
            "available": True,
            "trend": trend,
            "pattern": pattern,
            "pattern_confidence": pattern_conf,
            "support_levels": support_levels,
            "resistance_levels": resistance_levels,
            "confidence": confidence,
            "reasoning": reasons,
            "indicators": {
                "rsi_14": snapshot.get("rsi_14"),
                "sma_7": snapshot.get("sma_7"),
                "sma_20": snapshot.get("sma_20"),
                "macd_hist": snapshot.get("macd_hist"),
                "bb_upper": snapshot.get("bb_upper"),
                "bb_lower": snapshot.get("bb_lower"),
            },
            "method": "deterministic_rule_fusion (not an LLM)",
        },
        source="geckoterminal+clickhouse",
        confidence=0.7 if candles else 0.0,
    )
