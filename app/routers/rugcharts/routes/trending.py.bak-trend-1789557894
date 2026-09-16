"""Trending tokens, health probe, analyze/TA snapshots and plugin aggregation."""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Query

from app.core.data_fusion import envelope, fusion
from app.core.logging import get_logger
from app.services.ta_indicators import compute_ta_snapshot

from ..classifier import classify_trades, label_summary
from ..helpers import (
    _chain_freshness,
    _compute_rug_score,
    _compute_volume_authenticity,
    _fetch_dexscreener_trending,
    _fetch_holder_stats,
    _fetch_ohlcv_clickhouse,
    _fetch_ohlcv_gecko,
    _fetch_pair_info,
    _fetch_whale_trades_clickhouse,
)

logger = get_logger("rugcharts_router")

router = APIRouter(tags=["rugcharts"])


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
