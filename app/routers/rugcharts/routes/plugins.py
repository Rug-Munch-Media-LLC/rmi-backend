"""RugCharts plugin aggregation.

Composes REAL data from the existing rugcharts helpers — each plugin entry
carries its data or an explicit ``error``; nothing is fabricated.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query

from ..classifier import classify_trades, label_summary
from ..helpers import (
    _compute_rug_score,
    _compute_volume_authenticity,
    _fetch_holder_stats,
    _fetch_ohlcv_clickhouse,
    _fetch_pair_info,
    _fetch_whale_trades_clickhouse,
)

router = APIRouter(tags=["rugcharts"])

PLUGIN_NAMES = (
    "wallet_flow",
    "risk_heatmap",
    "supply_donut",
    "wash_trading",
    "token_health",
)
UNAVAILABLE_PLUGINS = (
    "contract_risk",
    "social_divergence",
    "holder_evolution",
    "fund_flow",
    "cluster_activity",
)


async def _guarded(coro: Any) -> dict[str, Any]:
    """Each plugin degrades alone: a failure is reported, never raised."""
    try:
        return await asyncio.wait_for(coro, timeout=15.0)
    except Exception as exc:
        return {"error": str(exc)[:200]}


async def _wallet_flow(token_address: str, chain: str) -> dict[str, Any]:
    trades = await _fetch_whale_trades_clickhouse(token_address, chain, 100)
    labeled = classify_trades(trades)
    return {
        "trades": labeled[:50],
        "buy_count": sum(1 for t in labeled if t["side"] == "buy"),
        "sell_count": sum(1 for t in labeled if t["side"] == "sell"),
        "total_whale_usd": round(
            sum(float(t.get("volume_usd", 0)) for t in labeled), 2
        ),
        "label_summary": label_summary(labeled),
        "count": len(labeled),
    }


async def _risk_heatmap(token_address: str, chain: str) -> dict[str, Any]:
    pair_info = await _fetch_pair_info(token_address, chain)
    candles = await _fetch_ohlcv_clickhouse(token_address, chain, "1h", 168)
    return _compute_rug_score(
        pair_info, _compute_volume_authenticity(pair_info, candles)
    )


async def _supply_donut(token_address: str, chain: str) -> dict[str, Any]:
    stats = await _fetch_holder_stats(token_address, chain)
    if not stats:
        return {"error": "no holder data available"}
    return stats


async def _wash_trading(token_address: str, chain: str) -> dict[str, Any]:
    pair_info = await _fetch_pair_info(token_address, chain)
    candles = await _fetch_ohlcv_clickhouse(token_address, chain, "1h", 168)
    return _compute_volume_authenticity(pair_info, candles)


async def _token_health(token_address: str, chain: str) -> dict[str, Any]:
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


COLLECTORS = (_wallet_flow, _risk_heatmap, _supply_donut, _wash_trading, _token_health)


@router.get("/plugins/all")
async def rugcharts_plugins_all(
    token_address: str,
    chain: str = Query("solana"),
) -> dict[str, Any]:
    """Aggregate plugin analysis for the RugCharts PluginViewer."""
    results = await asyncio.gather(
        *(_guarded(collect(token_address, chain)) for collect in COLLECTORS)
    )
    plugins = dict(zip(PLUGIN_NAMES, results, strict=True))
    # Plugins the panel expects but we have no real data source for yet are
    # reported as unavailable — never stubbed with fake numbers.
    for name in UNAVAILABLE_PLUGINS:
        plugins[name] = {"error": "not_available"}
    return {
        "token_address": token_address,
        "chain": chain,
        "plugins": plugins,
        "available": [k for k, v in plugins.items() if not v.get("error")],
    }
