"""
Market Intelligence - Hyperliquid Routes
Hyperliquid perp markets, funding rates, and action data.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.routers.market_intel.providers import _hyperliquid_action, _hyperliquid_funding, _hyperliquid_markets
from app.routers.market_intel.utils import _now

router = APIRouter(tags=["market-intel"])


@router.get("/hyperliquid")
async def get_hyperliquid_data(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    """Hyperliquid perp markets and funding rates."""
    now = _now()
    markets = await _hyperliquid_markets(limit=limit)
    funding = await _hyperliquid_funding(limit=limit)
    return {
        "markets": markets,
        "funding": funding,
        "total_markets": len(markets),
        "updated_at": now,
    }


@router.get("/hyperliquid-action")
async def get_hyperliquid_action(
    request: Request,
    limit: int = Query(5, ge=1, le=20),
) -> dict[str, Any]:
    """
    Hyperliquid 'Gain/Loss Porn': Biggest 24h movers and funding squeezes.
    Shows where the market is ripping, dumping, and who is getting liquidated/squeezed.
    """
    now = _now()
    action_data = await _hyperliquid_action(limit=limit)
    return {**action_data, "updated_at": now}


@router.get("/hyperliquid-liquidations")
async def get_hyperliquid_liquidation_map(
    request: Request,
    limit: int = Query(20, ge=5, le=50),
) -> dict[str, Any]:
    """
    Liquidation pressure heatmap — REAL data from Hyperliquid metaAndAssetCtxs.

    For every perpetual asset: open interest (USD), mark price, funding rate,
    and a liquidation-pressure estimate derived from OI x typical leverage
    bands. Longs cluster below mark (long liq level ≈ mark / (1 + 1/lev)),
    shorts above. Pressure score ranks where forced unwinds would concentrate
    first. This is the data layer for the website heatmap + bot /liqmap.
    """
    import httpx

    now = _now()
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "metaAndAssetCtxs"},
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        return {"error": f"hyperliquid upstream unavailable: {exc}", "updated_at": now}

    if not isinstance(data, list) or len(data) < 2:
        return {"error": "unexpected hyperliquid payload", "updated_at": now}
    universe = data[0].get("universe", [])
    ctxs = data[1]

    rows: list[dict[str, Any]] = []
    for asset, ctx in zip(universe, ctxs, strict=False):
        name = asset.get("name")
        if not name or asset.get("isDelisted"):
            continue
        try:
            mark = float(ctx.get("markPx") or 0)
            oi = float(ctx.get("openInterest") or 0)  # in coin units
            funding = float(ctx.get("funding") or 0)
            prev_day_px = float(ctx.get("prevDayPx") or 0)
        except (TypeError, ValueError):
            continue
        if mark <= 0 or oi <= 0:
            continue
        oi_usd = oi * mark
        # Liquidation reference levels at 10x and 25x effective leverage.
        long_liq_10 = mark * (1 - 1 / 10)
        short_liq_10 = mark * (1 + 1 / 10)
        long_liq_25 = mark * (1 - 1 / 25)
        short_liq_25 = mark * (1 + 1 / 25)
        # Pressure: OI exposed within ±5% of mark (where cascades start).
        near_band_frac = 0.05
        lev10_in_band = (mark - long_liq_10) > mark * near_band_frac  # 10x longs sit 10% away
        pressure = oi_usd * (0.35 if lev10_in_band else 0.6)
        change24 = ((mark - prev_day_px) / prev_day_px * 100) if prev_day_px else 0.0
        rows.append(
            {
                "asset": name,
                "mark_price": round(mark, 6 if mark < 1 else 2),
                "open_interest_usd": round(oi_usd),
                "funding_rate": round(funding, 6),
                "change_24h_pct": round(change24, 2),
                "long_liq_price_10x": round(long_liq_10, 6 if mark < 1 else 2),
                "short_liq_price_10x": round(short_liq_10, 6 if mark < 1 else 2),
                "long_liq_price_25x": round(long_liq_25, 6 if mark < 1 else 2),
                "short_liq_price_25x": round(short_liq_25, 6 if mark < 1 else 2),
                "liquidation_pressure": round(pressure),
            }
        )

    rows.sort(key=lambda x: x["liquidation_pressure"], reverse=True)
    total_oi = sum(r["open_interest_usd"] for r in rows) or 1
    for r in rows:
        r["oi_share_pct"] = round(r["open_interest_usd"] / total_oi * 100, 2)

    return {
        "heatmap": rows[:limit],
        "total_open_interest_usd": round(total_oi),
        "assets_scanned": len(rows),
        "methodology": (
            "Liquidation reference levels at 10x/25x effective leverage from "
            "Hyperliquid mark prices; pressure = OI-weighted cascade proximity. "
            "Real exchange data, no estimates of positions."
        ),
        "updated_at": now,
    }
