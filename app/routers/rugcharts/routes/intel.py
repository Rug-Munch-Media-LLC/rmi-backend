"""Token intelligence, whale trades and volume authenticity analysis."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.data_fusion import envelope, fusion
from app.core.logging import get_logger
from app.services.ta_indicators import compute_ta_snapshot

from ..classifier import classify_trades, label_summary, volume_breakdown
from ..helpers import (
    _compute_rug_score,
    _compute_volume_authenticity,
    _fetch_ohlcv_gecko,
    _fetch_pair_info,
    _fetch_recent_trades_clickhouse,
    _fetch_whale_trades_clickhouse,
)
from ..helpers.bias import predictive_bias

logger = get_logger("rugcharts_router")

router = APIRouter(tags=["rugcharts"])


@router.get("/intel/{chain}/{pair_address}")
async def rugcharts_intel(
    chain: str,
    pair_address: str,
) -> Any:
    """Get comprehensive intelligence on a token pair.

    Fuses GeckoTerminal OHLCV, DexScreener pair info, volume authenticity,
    shared-module TA and predictive heuristics through the DataFusion engine
    into a consolidated intelligence brief with confidence scoring.
    """
    pair_info = await _fetch_pair_info(pair_address, chain)
    candles = await _fetch_ohlcv_gecko(pair_address, chain, "1h", 48)
    vol_auth = _compute_volume_authenticity(pair_info, candles)
    rug_score = _compute_rug_score(pair_info, vol_auth)

    ta = compute_ta_snapshot(candles)

    # Heuristic signals — rule-based, honestly labeled as such (not AI).
    predictions = []
    if ta.get("available"):
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
            "prediction_method": "rule_based_heuristics",
            "intel_brief": intel_brief,
            "chain": chain,
            "pair_address": pair_address,
            "fallback_used": not bool(pair_info),
        },
        source="geckoterminal+dexscreener+datafusion",
        confidence=fused_risk.get("confidence", 0.3),
    )


@router.get("/whales/{chain}/{token_address}")
async def rugcharts_whales(
    chain: str,
    token_address: str,
    limit: int = Query(100, ge=1, le=500, description="Max whale trades to return"),
    min_usd: float = Query(
        0.0,
        ge=0.0,
        description="Optional minimum USD notional filter (0 = any whale-flagged event)",
    ),
) -> Any:
    """Whale trade markers + wallet-behavior labels for a token.

    Queries ``wallet_memory.dex_events`` for whale-flagged swaps where the
    token appears on either side of the pair. Each marker carries unix time,
    price, USD notional, tx hash, buy/sell side (V2 semantics; V3 =
    "unknown") AND a heuristic ``wallet_label`` (whale/bot/sniper/bundle/
    swap) computed by ``classifier.py`` from measurable trade-sequence
    properties — the labels the frontend trade-tape filters on.
    """
    trades = await _fetch_whale_trades_clickhouse(token_address, chain, limit, min_usd)
    labeled = classify_trades(trades)

    buys = [t for t in labeled if t["side"] == "buy"]
    sells = [t for t in labeled if t["side"] == "sell"]
    unknown = [t for t in labeled if t["side"] == "unknown"]
    total_whale_usd = round(sum(float(t.get("volume_usd", 0)) for t in labeled), 2)

    return envelope(
        success=True,
        data={
            "whale_trades": labeled,
            "buy_count": len(buys),
            "sell_count": len(sells),
            "unknown_count": len(unknown),
            "total_whale_usd": total_whale_usd,
            "label_summary": label_summary(labeled),
            "count": len(labeled),
            "chain": chain,
            "token_address": token_address,
            "min_usd": min_usd,
            "limit": limit,
        },
        source="lite_chain_indexer (ClickHouse dex_events)",
        confidence=0.8 if labeled else 0.0,
    )


@router.get("/volume-analysis/{chain}/{token_address}")
async def rugcharts_volume_analysis(
    chain: str,
    token_address: str,
    limit: int = Query(200, ge=10, le=500, description="Recent trades to analyze"),
) -> Any:
    """Aggregate bot/wash-volume analysis — the metric DexScreener lacks.

    Classifies the token's most recent dex trades (own indexer data) and
    reports what share of traded volume is machine-generated vs organic.
    Definitions are explicit in the response (`method` field); confidence
    scales with sample size and thin samples return null percentages
    rather than guesses.
    """
    trades = await _fetch_recent_trades_clickhouse(token_address, chain, limit)
    labeled = classify_trades(trades)
    breakdown = volume_breakdown(labeled)

    return envelope(
        success=True,
        data={
            "chain": chain,
            "token_address": token_address,
            **breakdown,
            "predictive_bias": predictive_bias(breakdown),
        },
        source="lite_chain_indexer (ClickHouse dex_events)",
        confidence=0.8 if breakdown["confidence"] == "high" else 0.5,
    )


# ── Volume breakdown: bot / wash / MEV / organic (DexScreener-killer) ────
# Maps classifier wallet labels onto the volume-authenticity taxonomy the
# frontend BotVolumeLabeler + VolumeAuthenticityGauge render:
#   bot    -> bot      (machine-gun / round-number sizing)
#   bundle -> wash     (coordinated same-second launch volume)
#   sniper -> mev      (buy-then-flip inside the sniper window)
#   whale / swap -> organic
_VOLUME_LABEL_MAP = {
    "bot": "bot",
    "bundle": "wash",
    "sniper": "mev",
    "whale": "organic",
    "swap": "organic",
}


@router.get("/volume-breakdown/{chain}/{address}")
async def rugcharts_volume_breakdown(
    chain: str,
    address: str,
    limit: int = Query(200, ge=10, le=500, description="Recent trades to classify"),
) -> Any:
    """Per-trade volume authenticity breakdown with labeled trades.

    Extends /volume-analysis with the exact shape the RugCharts
    BotVolumeLabeler and VolumeAuthenticityGauge consume: every recent
    trade carries a bot/wash/mev/organic label + confidence, and the
    aggregate reports organic/bot/wash/mev volume shares plus the
    statistical volume-authenticity score.
    """
    trades = await _fetch_recent_trades_clickhouse(address, chain, limit)
    labeled = classify_trades(trades)
    breakdown = volume_breakdown(labeled)
    pair_info = await _fetch_pair_info(address, chain)
    vol_auth = _compute_volume_authenticity(pair_info, [])

    labeled_trades: list[dict[str, Any]] = []
    pct_totals: dict[str, float] = {"organic": 0.0, "bot": 0.0, "wash": 0.0, "mev": 0.0}
    for t in labeled:
        label = _VOLUME_LABEL_MAP.get(t.get("wallet_label", "swap"), "organic")
        pct_totals[label] += float(t.get("volume_usd", 0) or 0)
        labeled_trades.append(
            {
                "timestamp": t.get("time", 0),
                "side": t.get("side", "unknown"),
                "amount": round(float(t.get("volume_usd", 0) or 0), 2),
                "price_usd": float(t.get("price_usd", 0) or 0),
                "label": label,
                "confidence": t.get("label_confidence", 40),
                "tx_hash": t.get("tx_hash", ""),
            }
        )

    total_vol = sum(pct_totals.values())
    shares = {k: round(v / total_vol * 100, 1) if total_vol > 0 else None for k, v in pct_totals.items()}

    return envelope(
        success=True,
        data={
            "chain": chain,
            "token_address": address,
            "organic_pct": shares["organic"],
            "bot_pct": shares["bot"],
            "wash_pct": shares["wash"],
            "mev_pct": shares["mev"],
            "authenticity_score": vol_auth["authentic_score"],
            "risk_flags": vol_auth["risk_flags"],
            "sample": breakdown.get("sample", {}),
            "confidence": breakdown.get("confidence", "none"),
            "labeled_trades": labeled_trades,
        },
        source="lite_chain_indexer (ClickHouse dex_events)",
        confidence=0.8 if breakdown.get("confidence") == "high" else 0.5,
    )
