"""Cross-venue liquidation pressure map.

Per asset: total OI across venues, per-venue OI share, funding spread, and
10x liquidation reference levels computed off the volume-weighted mark. Ranked
by aggregate cascade pressure. All venues fail independently, so one venue
being down or geo-blocked only removes it from the map.

The per-venue snapshots live in ``liqmap_venues`` (the geo-blocked ones) and
``liqmap_venues_direct`` (the ones that answer our egress directly).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request

from app.routers.market_intel.providers import _hyperliquid_markets
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
    bybit = bybit if isinstance(bybit, dict) else {}
    binance = binance if isinstance(binance, dict) else {}
    okx = okx if isinstance(okx, dict) else {}
    bitget = bitget if isinstance(bitget, dict) else {}
    gate = gate if isinstance(gate, dict) else {}
    mexc = mexc if isinstance(mexc, dict) else {}
    kucoin = kucoin if isinstance(kucoin, dict) else {}

    try:
        hl_markets = await hl_task
    except Exception:
        hl_markets = []

    assets: dict[str, dict[str, Any]] = {}
    for m in hl_markets:
        sym = str(m.get("asset") or m.get("symbol") or "").upper()
        if not sym:
            continue
        row = assets.setdefault(sym, {"venues": {}, "total_oi_usd": 0.0})
        oi = float(m.get("open_interest_usd") or 0)
        row["venues"]["Hyperliquid"] = {
            "oi_usd": round(oi),
            "funding": m.get("funding_rate"),
            "mark": m.get("mark_price"),
        }
        row["total_oi_usd"] += oi
        row.setdefault("marks", []).append(float(m.get("mark_price") or 0))
    for venue, snap in (
        ("Bybit", bybit),
        ("Binance", binance),
        ("OKX", okx),
        ("Bitget", bitget),
        ("Gate.io", gate),
        ("MEXC", mexc),
        ("KuCoin", kucoin),
    ):
        for sym, d in snap.items():
            row = assets.setdefault(sym, {"venues": {}, "total_oi_usd": 0.0})
            oi = float(d.get("oi_usd") or 0)
            row["venues"][venue] = {"oi_usd": round(oi), "funding": d.get("funding"), "mark": d.get("mark")}
            row["total_oi_usd"] += oi
            if d.get("mark"):
                row.setdefault("marks", []).append(float(d["mark"]))

    rows: list[dict[str, Any]] = []
    import re as _re

    # Ticker normalization: XBT is legacy BTC naming (Bitget/KuCoin).
    if "XBT" in assets and "BTC" in assets:
        btc = assets["BTC"]
        xbt = assets.pop("XBT")
        btc["total_oi_usd"] += xbt["total_oi_usd"]
        for venue, d in xbt["venues"].items():
            if venue not in btc["venues"]:
                btc["venues"][venue] = d
                btc["total_oi_usd"] = btc.get("total_oi_usd", 0)
        btc.setdefault("marks", []).extend(xbt.get("marks", []))

    for sym, row in assets.items():
        if not _re.fullmatch(r"[A-Z]{2,6}", sym):
            continue  # skip indices (NAS100), dated pairs, malformed keys
        marks = [m for m in row.get("marks", []) if m > 0]
        if not marks or row["total_oi_usd"] <= 0:
            continue
        mark = sum(marks) / len(marks)
        long10 = mark * (1 - 1 / 10)
        short10 = mark * (1 + 1 / 10)
        fundings = [v["funding"] for v in row["venues"].values() if isinstance(v.get("funding"), (int, float))]
        rows.append(
            {
                "asset": sym,
                "total_oi_usd": round(row["total_oi_usd"]),
                "venue_count": len(row["venues"]),
                "venues": row["venues"],
                "weighted_mark": round(mark, 6 if mark < 1 else 2),
                "long_liq_10x": round(long10, 6 if mark < 1 else 2),
                "short_liq_10x": round(short10, 6 if mark < 1 else 2),
                "funding_spread": round(max(fundings) - min(fundings), 6) if len(fundings) > 1 else None,
                "pressure": round(row["total_oi_usd"]),
            }
        )
    rows.sort(key=lambda x: x["pressure"], reverse=True)
    total = sum(r["total_oi_usd"] for r in rows) or 1
    for r in rows:
        r["oi_share_pct"] = round(r["total_oi_usd"] / total * 100, 2)

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
