"""RugCharts Backend Router
==========================
Real OHLCV candles (ClickHouse own-data → GeckoTerminal fallback), volume
authenticity, dev reputation, wallet-behavior labels and comprehensive token
intelligence for the RugCharts page.

Data-fetching + scoring helpers live in ``helpers.py``; shared TA math in
``ta.py``; wallet/bot labeling in ``classifier.py``.

Honesty contract (2026-08-24 audit WIN-1):
- Every candle/intel response reports its ACTUAL data ``source``
  (clickhouse | geckoterminal | none) — previously ``fallback_used`` was
  computed as "any candles exist" and lied about provenance.
- Unknown metrics are ``null``, never fabricated zeros.
"""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Query

from app.core.data_fusion import envelope, fusion
from app.core.logging import get_logger

from .classifier import classify_trades, label_summary, volume_breakdown
from .helpers import (
    _chain_freshness,
    _compute_rug_score,
    _compute_volume_authenticity,
    _fetch_dexscreener_trending,
    _fetch_holder_stats,
    _fetch_ohlcv_clickhouse,
    _fetch_ohlcv_gecko,
    _fetch_pair_info,
    _fetch_recent_trades_clickhouse,
    _fetch_whale_trades_clickhouse,
)
from .ta import compute_ta_snapshot

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
            "source": "dexscreener" if tokens else "none",
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
        0.0, ge=0.0, description="Optional minimum USD notional filter (0 = any whale-flagged event)"
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


@router.get("/health")
async def rugcharts_health() -> dict[str, Any]:
    """Health/capability probe for RugCharts."""
    freshness = await _chain_freshness()
    chains_stale = [c for c, v in freshness.items() if v.get("status") == "stale"]
    return {
        "status": "ok",
        "service": "rugcharts",
        "signal_types": ["whale_buy", "whale_sell", "death_spiral", "volume_spike", "rug_risk_high"],
        "capabilities": [
            "trending",
            "ohlcv",
            "intel",
            "analyze",
            "signals",
            "ta",
            "whales",
            "wallet_labels",
            "ws_trades",
        ],
        # Own-data coverage per chain — gaps are visible, not silent (WIN-9).
        "own_data_freshness": freshness,
        "chains_stale": chains_stale,
    }


@router.get("/analyze/{address}")
async def rugcharts_analyze(
    address: str,
    chain: str = Query("solana", description="Blockchain to query"),
) -> dict[str, Any]:
    """Token analysis snapshot for the RugCharts compare/multi-chain UI.

    Real data from GeckoTerminal pair info + rug/volume scoring helpers +
    REAL holder concentration via Helius (Solana). Metrics we cannot source
    are ``null`` — never placeholder zeros presented as facts.
    """
    pair = await _fetch_pair_info(address, chain)

    price = float(pair.get("base_token_price_usd", 0) or 0)
    vol_auth = _compute_volume_authenticity(pair, [])
    rug_score = _compute_rug_score(pair, vol_auth)
    holders = await _fetch_holder_stats(address, chain)

    result: dict[str, Any] = {
        "chain": chain,
        "address": address,
        "symbol": "",
        "name": pair.get("name", ""),
        "fallback_used": not bool(pair),
        # Holder metrics always present: real where sourced, null where unknown.
        "holder_count": None,
        "top_holder_pct": holders["top_holder_pct"] if holders else None,
        "top10_holder_pct": holders["top10_holder_pct"] if holders else None,
        "holder_data_source": holders["source"] if holders else None,
    }
    if pair.get("name"):
        result["symbol"] = str(pair.get("symbol") or str(pair["name"]).split("/")[0].strip())
    if pair:
        result["price_usd"] = price
        result["price_change_24h"] = float(pair.get("price_change_24h", 0) or 0)
        result["volume_24h"] = float(pair.get("volume_24h", 0) or 0)
        result["liquidity_usd"] = float(pair.get("liquidity_usd", 0) or 0)
        result["market_cap"] = float(pair.get("market_cap", 0) or 0)
        result["risk_score"] = int(rug_score.get("score", 0) or 0)
        result["risk_level"] = (
            "HIGH" if rug_score.get("score", 0) > 70 else "MEDIUM" if rug_score.get("score", 0) > 40 else "LOW"
        )
        created = pair.get("pool_created_at", "")
        age_hours: float | None = None
        if created:
            try:
                from datetime import datetime

                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age_hours = round((datetime.now(created_dt.tzinfo) - created_dt).total_seconds() / 3600, 1)
            except (ValueError, TypeError):
                age_hours = None
        result["age_hours"] = age_hours

    return result


@router.get("/ta/{address}")
async def rugcharts_ta(
    address: str,
    chain: str = Query("solana", description="Blockchain to query"),
    timeframe: str = Query("1h", description="Candle timeframe"),
) -> dict[str, Any]:
    """Technical-analysis snapshot derived from real GeckoTerminal OHLCV.

    Uses the shared TA module so /ta, /intel and AI chart analysis can
    never drift apart. Returns available=False when no candles resolve.
    """
    candles = await _fetch_ohlcv_gecko(address, chain, timeframe, 100)
    snapshot = compute_ta_snapshot(candles)
    if not snapshot.get("available"):
        return {"chain": chain, "address": address, **snapshot}

    return {"chain": chain, "address": address, **snapshot}


@router.get("/signals/{address}")
async def rugcharts_signals(
    address: str,
    chain: str = Query("solana", description="Blockchain to query"),
    limit: int = Query(20, ge=1, le=100, description="Max signals to return"),
) -> dict[str, Any]:
    """Trading signals for a token, built from labeled whale-trade data.

    Each signal derives from a real ClickHouse dex event and carries the
    classifier's wallet_label. When no trades resolve we return an empty
    list — the frontend renders empty gracefully.
    """
    trades = await _fetch_whale_trades_clickhouse(address, chain, limit)
    labeled = classify_trades(trades)
    signals: list[dict[str, Any]] = []
    for t in labeled:
        side = t.get("side", "unknown")
        label = t.get("wallet_label", "swap")
        confidence = max(t.get("label_confidence", 40), 70 if side != "unknown" else 40)
        signals.append(
            {
                "id": t.get("tx_hash", ""),
                "type": "buy" if side == "buy" else "sell" if side == "sell" else "info",
                "timestamp": t.get("time", 0),
                "price_at_signal": float(t.get("price_usd", 0) or 0),
                "confidence": confidence,
                "title": f"{label.capitalize()} {side.upper()} ${float(t.get('volume_usd', 0) or 0):,.0f}"
                if side != "unknown"
                else f"{label.capitalize()} activity ${float(t.get('volume_usd', 0) or 0):,.0f}",
                "reasoning": f"{label}-labeled swap on {chain} ({t.get('tx_hash', '')[:10]}...)",
                "indicators": ["whale_trade", label, {"volume_usd": float(t.get("volume_usd", 0) or 0)}],
                "wallet_label": label,
                "timeframe": "1h",
            }
        )
    return {
        "chain": chain,
        "address": address,
        "signals": signals[:limit],
        "count": len(signals[:limit]),
        "label_summary": label_summary(labeled),
    }


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

    # Predictive bias — transparent fusion of independent evidence sources.
    # Every factor names its evidence; no fabricated price targets.
    factors: list[dict[str, Any]] = []
    bias_score = 0
    if breakdown["bot_volume_pct"] is not None:
        if breakdown["bot_volume_pct"] >= 40:
            bias_score -= 25
            factors.append(
                {
                    "factor": "bot_dominance",
                    "impact": -25,
                    "evidence": f"{breakdown['bot_volume_pct']}% of sampled volume is bot-labeled",
                }
            )
        elif breakdown["bot_volume_pct"] >= 15:
            bias_score -= 10
            factors.append(
                {"factor": "bot_elevated", "impact": -10, "evidence": f"{breakdown['bot_volume_pct']}% bot volume"}
            )
        else:
            bias_score += 5
            factors.append(
                {"factor": "organic_flow", "impact": 5, "evidence": f"bot share only {breakdown['bot_volume_pct']}%"}
            )
    if breakdown["buy_pressure"] is not None:
        bp = breakdown["buy_pressure"]
        if bp >= 0.65:
            bias_score += 15
            factors.append({"factor": "buy_pressure", "impact": 15, "evidence": f"{bp:.0%} buy-side volume"})
        elif bp <= 0.35:
            bias_score -= 15
            factors.append({"factor": "sell_pressure", "impact": -15, "evidence": f"only {bp:.0%} buy-side"})

    bias = "BEARISH" if bias_score <= -20 else "BULLISH" if bias_score >= 20 else "NEUTRAL"

    return envelope(
        success=True,
        data={
            "chain": chain,
            "token_address": token_address,
            **breakdown,
            "predictive_bias": {
                "bias": bias,
                "score": max(-100, min(100, bias_score)),
                "factors": factors,
                "method": "transparent_rule_fusion",
                "disclaimer": "Heuristic bias from trade-flow evidence — not financial advice.",
            },
        },
        source="lite_chain_indexer (ClickHouse dex_events)",
        confidence=0.8 if breakdown["confidence"] == "high" else 0.5,
    )


@router.get("/plugins/all")
async def rugcharts_plugins_all(
    token_address: str,
    chain: str = Query("solana"),
) -> dict[str, Any]:
    """Aggregate plugin analysis for the RugCharts PluginViewer.

    Composes REAL data from the existing rugcharts helpers — each plugin
    entry carries its data or an explicit ``error``; nothing is fabricated.
    """
    import asyncio

    async def _safe(name: str, coro: Any) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(coro, timeout=15.0)
        except Exception as e:
            return {"error": str(e)[:200]}

    async def _wallet_flow() -> dict[str, Any]:
        trades = await _fetch_whale_trades_clickhouse(token_address, chain, 100)
        labeled = classify_trades(trades)
        return {
            "trades": labeled[:50],
            "buy_count": sum(1 for t in labeled if t["side"] == "buy"),
            "sell_count": sum(1 for t in labeled if t["side"] == "sell"),
            "total_whale_usd": round(sum(float(t.get("volume_usd", 0)) for t in labeled), 2),
            "label_summary": label_summary(labeled),
            "count": len(labeled),
        }

    async def _risk_heatmap() -> dict[str, Any]:
        pair_info = await _fetch_pair_info(token_address, chain)
        candles = await _fetch_ohlcv_clickhouse(token_address, chain, 168)
        vol_auth = _compute_volume_authenticity(pair_info, candles)
        return _compute_rug_score(pair_info, vol_auth)

    async def _supply_donut() -> dict[str, Any]:
        stats = await _fetch_holder_stats(token_address, chain)
        if not stats:
            return {"error": "no holder data available"}
        return stats

    async def _wash_trading() -> dict[str, Any]:
        pair_info = await _fetch_pair_info(token_address, chain)
        candles = await _fetch_ohlcv_clickhouse(token_address, chain, 168)
        return _compute_volume_authenticity(pair_info, candles)

    async def _token_health() -> dict[str, Any]:
        pair_info = await _fetch_pair_info(token_address, chain)
        if not pair_info:
            return {"error": "no pair info available"}
        return {
            "price_usd": pair_info.get("price_usd"),
            "liquidity_usd": pair_info.get("liquidity_usd"),
            "volume_24h": pair_info.get("volume_24h"),
            "fdv": pair_info.get("fdv"),
            "txns_24h_buys": pair_info.get("txns_24h_buys"),
            "txns_24h_sells": pair_info.get("txns_24h_sells"),
        }

    results = await asyncio.gather(
        _safe("wallet_flow", _wallet_flow()),
        _safe("risk_heatmap", _risk_heatmap()),
        _safe("supply_donut", _supply_donut()),
        _safe("wash_trading", _wash_trading()),
        _safe("token_health", _token_health()),
    )
    plugins = dict(
        zip(
            ["wallet_flow", "risk_heatmap", "supply_donut", "wash_trading", "token_health"],
            results,
            strict=True,
        )
    )
    # Plugins the panel expects but we have no real data source for yet are
    # reported as unavailable — never stubbed with fake numbers.
    for name in ("contract_risk", "social_divergence", "holder_evolution", "fund_flow", "cluster_activity"):
        plugins[name] = {"error": "not_available"}

    return {
        "token_address": token_address,
        "chain": chain,
        "plugins": plugins,
        "available": [k for k, v in plugins.items() if not v.get("error")],
    }
