"""

RugCharts Backend Router
========================
Real OHLCV candles via GeckoTerminal, volume authenticity, dev reputation,
and comprehensive token intelligence for the RugCharts page.

Data-fetching + scoring helpers live in ``app/routers/rugcharts_helpers.py``
(split out 2026-08-07).
"""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Query

from app.core.data_fusion import envelope, fusion
from app.core.logging import get_logger

from .rugcharts_helpers import (
    _compute_rug_score,
    _compute_volume_authenticity,
    _fetch_dexscreener_trending,
    _fetch_ohlcv_clickhouse,
    _fetch_ohlcv_gecko,
    _fetch_pair_info,
)

logger = get_logger("rugcharts_router")

router = APIRouter(prefix="/api/v1/rugcharts", tags=["rugcharts"])


@router.get("/trending")
async def rugcharts_trending(
    chain: str = Query("solana", description="Blockchain to query"),
    limit: int = Query(30, description="Max tokens to return"),
) -> Any:
    """Get trending tokens sorted by volume for RugCharts.

    Data sources: DexScreener (boosted tokens + latest profiles).
    Fused with DataFusion engine for multi-source confidence.
    """
    tokens, fallback_used = await _fetch_dexscreener_trending(chain, limit)

    # Fuse price data across all returned tokens for consistency
    now = datetime.datetime.now(datetime.UTC)
    for token in tokens[:limit]:
        price_data = {
            "source": "dexscreener",
            "price_usd": token.get("price_usd", 0),
            "freshness": now.isoformat(),
            "confidence": 0.5 if token.get("volume_24h", 0) > 1000 else 0.3,
        }
        fused = await fusion.fuse_price_data(price_data)
        token["fusion"] = {
            "price_confidence": fused.get("confidence", 0),
            "price_sources": fused.get("sources_used", 1),
        }

    return envelope(
        success=True,
        data={
            "tokens": tokens[:limit],
            "count": len(tokens),
            "chain": chain,
            "fallback_used": fallback_used,
        },
        source="dexscreener",
        confidence=0.5 if not fallback_used else 0.25,
    )


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
    or for sub-hourly timeframes. Fuses the result with DexScreener pair
    info via DataFusion for cross-referenced price confidence and volume
    authenticity scoring.
    """
    now = datetime.datetime.now(datetime.UTC)
    candles = await _fetch_ohlcv_clickhouse(pair_address, chain, timeframe, limit)
    if not candles:
        candles = await _fetch_ohlcv_gecko(pair_address, chain, timeframe, limit)
    pair_info = await _fetch_pair_info(pair_address, chain)
    vol_auth = _compute_volume_authenticity(pair_info, candles)

    # Summary from candles
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
    # If we have OHLCV close, add it as an additional price data point
    candle_price = (
        {
            "source": "geckoterminal_ohlcv",
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

    # ── Enrich response with fusion metadata ──
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
            "fallback_used": not bool(candles),
        },
        source="geckoterminal+coingecko",
        confidence=fused_price.get("confidence", 0.3),
    )


@router.get("/intel/{chain}/{pair_address}")
async def rugcharts_intel(
    chain: str,
    pair_address: str,
) -> Any:
    """Get comprehensive intelligence on a token pair.

    Fuses GeckoTerminal OHLCV, DexScreener pair info, volume authenticity,
    and TA predictions through the DataFusion engine for a consolidated
    intelligence brief with confidence scoring.
    """
    pair_info = await _fetch_pair_info(pair_address, chain)
    candles = await _fetch_ohlcv_gecko(pair_address, chain, "1h", 48)
    vol_auth = _compute_volume_authenticity(pair_info, candles)
    rug_score = _compute_rug_score(pair_info, vol_auth)

    # Simple TA from candles
    ta = {}
    if len(candles) >= 20:
        closes = [c["close"] for c in candles]
        sma20 = sum(closes[-20:]) / 20
        sma7 = sum(closes[-7:]) / 7
        gains, losses = [], []
        for i in range(-14, 0):
            diff = closes[i] - closes[i - 1]
            gains.append(max(0, diff))
            losses.append(max(0, -diff))
        avg_gain = sum(gains) / 14
        avg_loss = sum(losses) / 14
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - (100 / (1 + rs))
        bb_mean = sma20
        bb_std = (sum((c - bb_mean) ** 2 for c in closes[-20:]) / 20) ** 0.5
        bb_upper = bb_mean + 2 * bb_std
        bb_lower = bb_mean - 2 * bb_std
        vols = [c["volume"] for c in candles]
        vol_sma = sum(vols[-20:]) / 20
        vol_current = vols[-1] if vols else 0

        ta = {
            "sma_7": round(sma7, 10),
            "sma_20": round(sma20, 10),
            "rsi_14": round(rsi, 2),
            "bb_upper": round(bb_upper, 10),
            "bb_lower": round(bb_lower, 10),
            "bb_middle": round(bb_mean, 10),
            "volume_sma_20": round(vol_sma, 2),
            "volume_current": round(vol_current, 2),
            "price_vs_sma20": "ABOVE" if closes[-1] > sma20 else "BELOW",
            "rsi_signal": "OVERBOUGHT" if rsi > 70 else "OVERSOLD" if rsi < 30 else "NEUTRAL",
            "trend": "BULLISH" if sma7 > sma20 else "BEARISH",
            "volume_trend": "HIGH"
            if vol_current > vol_sma * 1.5
            else "LOW"
            if vol_current < vol_sma * 0.5
            else "NORMAL",
        }

    # Predictive signals
    predictions = []
    if ta:
        if ta.get("rsi_signal") == "OVERBOUGHT" and rug_score["score"] > 50:
            predictions.append(
                {
                    "signal": "DUMP_LIKELY",
                    "confidence": 75,
                    "reason": "Overbought RSI + high rug score",
                }
            )
        elif ta.get("rsi_signal") == "OVERSOLD" and vol_auth["authentic_score"] > 70:
            predictions.append(
                {
                    "signal": "BOUNCE_POSSIBLE",
                    "confidence": 60,
                    "reason": "Oversold with authentic volume",
                }
            )
        if ta.get("trend") == "BEARISH" and pair_info.get("txns_24h_sells", 0) > pair_info.get("txns_24h_buys", 0) * 2:
            predictions.append(
                {
                    "signal": "DEATH_SPIRAL",
                    "confidence": 70,
                    "reason": "Bearish trend + heavy selling",
                }
            )
        if vol_auth["authentic_score"] < 40:
            predictions.append(
                {
                    "signal": "FAKE_VOLUME",
                    "confidence": 80,
                    "reason": f"Volume authenticity only {vol_auth['authentic_score']}%",
                }
            )

    if not predictions:
        if rug_score["score"] > 60:
            predictions.append(
                {
                    "signal": "RUG_RISK_HIGH",
                    "confidence": 65,
                    "reason": "Multiple rug indicators present",
                }
            )
        else:
            predictions.append(
                {
                    "signal": "NO_CLEAR_SIGNAL",
                    "confidence": 50,
                    "reason": "Insufficient data for prediction",
                }
            )

    # ── Fuse into an intelligence brief via DataFusion ──
    fused_risk = await fusion.fuse_token_risk(
        additional=[
            {
                "source": "rugcharts",
                "risk_score": rug_score["score"],
                "risk_factors": [f["factor"] if isinstance(f, dict) else f for f in rug_score["factors"]],
                "confidence": 0.6 if rug_score["score"] > 0 else 0.2,
            },
            {
                "source": "volume_authenticity",
                "risk_score": 100 - vol_auth["authentic_score"],
                "risk_factors": vol_auth["risk_flags"],
                "confidence": 0.7 if vol_auth["authentic_score"] > 0 else 0.2,
            },
        ]
    )
    intel_brief = fusion.generate_intelligence_brief(fused_risk)

    return envelope(
        success=True,
        data={
            "pair_info": pair_info,
            "rug_score": rug_score,
            "volume_authenticity": vol_auth,
            "technical_analysis": ta,
            "predictions": predictions,
            "intel_brief": intel_brief,
            "chain": chain,
            "pair_address": pair_address,
            "fallback_used": not bool(pair_info),
        },
        source="geckoterminal+dexscreener+datafusion",
        confidence=fused_risk.get("confidence", 0.3),
    )
