"""Cross-venue liquidation pressure map.

Per asset: total OI across venues, per-venue OI share, funding spread, and
10x liquidation reference levels computed off the volume-weighted mark. Ranked
by aggregate cascade pressure. All venues fail independently, so one venue
being down or geo-blocked only removes it from the map.

The per-venue snapshots live in ``liqmap_venues`` (the geo-blocked ones) and
``liqmap_venues_direct`` (the ones that answer our egress directly). The
per-asset aggregation lives in ``liqmap_aggregate``; this module is the route.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request

from app.routers.market_intel.providers import _hyperliquid_markets
from app.routers.market_intel.routes.liqmap_aggregate import (
    build_rows,
    collect_assets,
    merge_xbt_into_btc,
    rank_rows,
)
from app.routers.market_intel.routes.liqmap_venues import (
    _binance_liquidation_snapshot,
    _bybit_liquidation_snapshot,
)
from app.routers.market_intel.routes.liqmap_venues_direct import (
    _bitget_liquidation_snapshot,
    _gate_liquidation_snapshot,
    _kucoin_liquidation_snapshot,
    _mexc_liquidation_snapshot,
    _okx_liquidation_snapshot,
)
from app.routers.market_intel.utils import _now

router = APIRouter(tags=["market-intel"])


@router.get("/liquidations-multi-venue")
async def get_multi_venue_liquidation_map(
    request: Request,
    limit: int = Query(20, ge=5, le=50),
) -> dict[str, Any]:
    """
    Cross-exchange liquidation pressure map: Hyperliquid + Bybit + Binance + OKX.

    Per asset: total OI across venues, per-venue OI share, funding spread,
    and 10x liquidation reference levels computed off the volume-weighted mark.
    Ranked by aggregate cascade pressure. All venues fail independently.
    """

    now = _now()
    hl_task = _hyperliquid_markets(limit=200)
    bybit, binance, okx, bitget, gate, mexc, kucoin = await asyncio.gather(
        _bybit_liquidation_snapshot(),
        _binance_liquidation_snapshot(),
        _okx_liquidation_snapshot(),
        _bitget_liquidation_snapshot(),
        _gate_liquidation_snapshot(),
        _mexc_liquidation_snapshot(),
        _kucoin_liquidation_snapshot(),
        return_exceptions=True,
    )
    venue_snapshots = (
        ("Bybit", bybit),
        ("Binance", binance),
        ("OKX", okx),
        ("Bitget", bitget),
        ("Gate.io", gate),
        ("MEXC", mexc),
        ("KuCoin", kucoin),
    )

    try:
        hl_markets = await hl_task
    except Exception:
        hl_markets = []

    assets = collect_assets(hl_markets, venue_snapshots)
    merge_xbt_into_btc(assets)
    rows, total = rank_rows(build_rows(assets))

    return {
        "heatmap": rows[:limit],
        "total_open_interest_usd": round(total),
        "assets_scanned": len(rows),
        "venues_online": sorted({v for r in rows for v in r["venues"]}),
        "methodology": (
            "Cross-venue OI aggregated from Hyperliquid, Bybit, Binance, OKX, "
            "Bitget, Gate.io, MEXC, KuCoin "
            "public endpoints; 10x liquidation levels off volume-weighted mark; "
            "funding spread shows dislocation between venues."
        ),
        "updated_at": now,
    }
