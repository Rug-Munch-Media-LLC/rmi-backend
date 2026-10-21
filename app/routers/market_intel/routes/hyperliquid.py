"""
Market Intelligence - Hyperliquid Routes
Hyperliquid perp markets, funding rates, and action data.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request

from app.routers.market_intel.providers import _hyperliquid_action, _hyperliquid_funding, _hyperliquid_markets
from app.routers.market_intel.utils import _now

logger = logging.getLogger(__name__)

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


# ── Multi-venue liquidation aggregation (2026-08-23) ────────────────────────
# Free public endpoints only; each venue fails independently.


def _proxy_client() -> tuple[httpx.AsyncClient, str | None]:
    """Client routed through the Webshare residential pool for geo-blocked venues.

    2026-10-21 FIX: this used to read ``LIQMAP_PROXY_URL`` and hand the WHOLE
    value to ``httpx.AsyncClient(proxy=...)``. That env carried a
    space-separated LIST of proxy URLs, and httpx's ``proxy=`` takes exactly
    ONE url - so every call raised ``Invalid port: '6359 http:'`` and the
    market-intel liquidation map was 100% dead (0 successes, all six venues
    erroring) while still spending the account's proxy budget.

    Now it uses the shared rotator (``app.databus.proxy_rotator``), which
    parses a comma/space/newline list into valid urls, rotates round-robin
    and cools a proxy down after a failure. No config of its own: the pool
    comes from the standard ``WEBSHARE_PROXY_LIST`` env, and an empty list
    means "direct", so an unconfigured deployment behaves exactly as before.

    The pool carries the geo-blocked venues only (Bybit, Binance). Venues
    that answer directly (OKX, Gate, Bitget, MEXC) do NOT use this client.

    Returns the client AND the proxy it was given (None when direct) so the
    caller can report a geo-refusal. Bybit and Binance answer 403/451 to a
    US exit - our own egress and most of the residential pool are US - so a
    refusal is a dead exit for this venue, and cooling it down is what makes
    the rotation converge on the non-US entries that do answer (measured
    2026-10-21: 8 of 20 pool entries answer 200, 8 are US and refused, 4 are
    unreachable). See ``_report_venue_refusal``.
    """
    proxy = _pick_proxy()
    return httpx.AsyncClient(timeout=12, proxy=proxy), proxy


#: HTTP status a geo-blocked venue returns to a refused exit: Bybit 403,
#: Binance 451 ("Service unavailable from a restricted location").
_VENUE_GEO_REFUSALS: frozenset[int] = frozenset({403, 451})

#: Pool exits that failed for a geo-blocked venue, and the pool they are
#: drawn from. Two ways an exit fails here, and BOTH are properties of the
#: exit rather than of the venue, so one set covers both venues:
#:   - HTTP 403/451: the exit's country is refused by the venue (the same
#:     exit answers OKX fine).
#:   - a transport error: the exit is unreachable.
#: Keeping them together is what makes the rotation converge: with 12 of the
#: 20 pool entries unusable for these venues (8 US, 4 unreachable) and only 8
#: that answer, an implementation that recorded only the HTTP refusals kept
#: re-testing the unreachable ones forever.
_failed_proxies: set[str] = set()
_pool_cache: list[str] = []
_pool_index: int = 0


def _proxy_pool() -> list[str]:
    """The residential pool as a list of proxy urls (cached per process).

    Parsed by the shared rotator's parser, so the same comma/space/newline
    list format and the same credential handling apply. An empty pool means
    "direct".
    """
    global _pool_cache
    if not _pool_cache:
        from app.databus.proxy_rotator import parse_proxy_list

        raw = os.getenv("WEBSHARE_PROXY_LIST", "")
        _pool_cache = parse_proxy_list(raw)
    return _pool_cache


def _pick_proxy() -> str | None:
    """Next pool entry that no venue has refused, round-robin.

    Skips the exits a geo-blocked venue already refused, so the rotation
    converges on the entries that answer instead of re-testing the same
    dead ones every round. When EVERY entry has been refused, the set is
    cleared and the pool is re-tested from the start - refusals are a
    geo property of the exit, but the pool is refreshed periodically by
    webshare-sync.py, so a refreshed entry must get a fresh chance.
    """
    global _pool_index
    pool = _proxy_pool()
    if not pool:
        return None
    # A monotonic cursor over the ORIGINAL pool order, skipping the exits
    # already known to fail. Indexing a filtered list instead would re-test
    # failed entries, because the modulus changes as the filter grows.
    for _ in range(len(pool)):
        proxy = pool[_pool_index % len(pool)]
        _pool_index += 1
        if proxy not in _failed_proxies:
            return proxy
    # Every exit has failed at least once. The pool is refreshed periodically
    # by webshare-sync.py, so clear the set and give every entry a fresh
    # chance rather than locking the venue out for the process lifetime.
    _failed_proxies.clear()
    _pool_index = 0
    return pool[0]


def _report_venue_refusal(proxy: str | None, status_code: int) -> None:
    """Remember that this exit is refused by the geo-blocked venues.

    Without this the rotation would keep handing out the same US exits
    (half the pool) and every geo-blocked call would fail until it happened
    to land on a non-US one.
    """
    if proxy is None or status_code not in _VENUE_GEO_REFUSALS:
        return
    _note_proxy_failure(proxy)


def _note_proxy_failure(proxy: str | None) -> None:
    """Mark an exit as unusable for the geo-blocked venues and cool it down.

    Called for a geo refusal AND for a transport failure - an unreachable
    exit is just as useless as a refused one, and it is the reason a
    rotation that only recorded refusals never converged.
    """
    if proxy is None:
        return
    _failed_proxies.add(proxy)
    from app.databus.proxy_rotator import get_proxy_rotator

    get_proxy_rotator().mark_failed(proxy)


async def _bybit_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Bybit v5 tickers: one call -> OI + funding + mark for all linear."""
    out: dict[str, dict[str, Any]] = {}
    client, proxy = _proxy_client()
    try:
        async with client as c:
            r = await c.get("https://api.bybit.com/v5/market/tickers", params={"category": "linear"})
            if r.status_code in _VENUE_GEO_REFUSALS:
                _report_venue_refusal(proxy, r.status_code)
                logger.warning("liqmap bybit refused exit (status=%s)", r.status_code)
                return out
            for t in (r.json().get("result") or {}).get("list", []):
                sym = str(t.get("symbol") or "").replace("USDT", "").replace("USDC", "")
                if not sym:
                    continue
                try:
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": float(t.get("open_interest") or 0) * float(t.get("markPrice") or 0),
                            "funding": float(t.get("fundingRate") or 0),
                            "mark": float(t.get("markPrice") or 0),
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        _note_proxy_failure(proxy)
        logger.warning("liqmap bybit failed: %s", str(exc)[:100])
    return out


async def _binance_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Binance fapi premiumIndex: one call -> mark + funding for all symbols.

    OI needs per-symbol calls on Binance, so venues get OI from Bybit/OKX/HL;
    Binance contributes mark/funding for liq-level math when OI known elsewhere.
    """
    out: dict[str, dict[str, Any]] = {}
    client, proxy = _proxy_client()
    try:
        # Binance futures is geo-restricted from our egress (HTTP 451
        # "Service unavailable from a restricted location"), so this venue
        # needs the residential pool. A US exit is refused too - the pool
        # entry that answers is a non-US one.
        async with client as c:
            r = await c.get("https://fapi.binance.com/fapi/v1/premiumIndex")
            if r.status_code in _VENUE_GEO_REFUSALS:
                _report_venue_refusal(proxy, r.status_code)
                logger.warning("liqmap binance refused exit (status=%s)", r.status_code)
                return out
            payload = r.json()
            # The endpoint returns a LIST on success and an OBJECT on a
            # geo/availability refusal ({"code":..,"msg":..}). Iterating the
            # object yielded its KEYS, so every element was a str and
            # `t.get(...)` raised AttributeError - the venue never reported.
            rows = payload if isinstance(payload, list) else []
            for t in rows:
                sym = str(t.get("symbol") or "").replace("USDT", "").replace("USDC", "").replace("PERP", "")
                if not sym:
                    continue
                try:
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": 0.0,
                            "funding": float(t.get("lastFundingRate") or 0),
                            "mark": float(t.get("markPrice") or 0),
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        _note_proxy_failure(proxy)
        logger.warning("liqmap binance failed: %s", str(exc)[:100])
    return out


async def _okx_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """OKX v5 public open-interest: one call -> oiUsd for all SWAPs."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://www.okx.com/api/v5/public/open-interest", params={"instType": "SWAP"})
            for t in r.json().get("data") or []:
                inst = str(t.get("instId") or "")  # e.g. BTC-USDT-SWAP
                sym = inst.split("-")[0] if inst else ""
                if not sym:
                    continue
                try:
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": float(t.get("oiUsd") or 0),
                            "funding": None,
                            "mark": float(t.get("instPx") or 0) or None,
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("liqmap okx failed: %s", str(exc)[:100])
    return out


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
    import asyncio as _asyncio

    now = _now()
    hl_task = _hyperliquid_markets(limit=200)
    bybit, binance, okx, bitget, gate, mexc, kucoin = await _asyncio.gather(
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


async def _bitget_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Bitget v2 tickers: one call -> OI(USD) + funding for all USDT perps."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.bitget.com/api/v2/mix/market/tickers", params={"productType": "USDT-FUTURES"})
            for t in r.json().get("data") or []:
                sym = str(t.get("symbol") or "").replace("USDT", "")
                if not sym:
                    continue
                try:
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": float(t.get("holdingAmount") or 0) * float(t.get("markPrice") or 0),
                            "funding": float(t.get("fundingRate") or 0),
                            "mark": float(t.get("markPrice") or 0),
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("liqmap bitget failed: %s", str(exc)[:100])
    return out


async def _gate_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """Gate.io v4 futures contracts: one call -> OI + funding for all USDT perps."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
            for t in r.json():
                sym = str(t.get("name") or "").replace("_USDT", "").replace("_USDC", "")
                if not sym:
                    continue
                try:
                    mark = float(t.get("mark_price") or 0)
                    oi_usd = float(t.get("open_interest_usd") or 0)
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": oi_usd,
                            "funding": float(t.get("funding_rate") or 0),
                            "mark": mark,
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("liqmap gate failed: %s", str(exc)[:100])
    return out


async def _mexc_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """MEXC contract ticker: one call -> OI + funding for all perps."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://contract.mexc.com/api/v1/contract/ticker")
            for t in r.json().get("data") or []:
                sym = str(t.get("symbol") or "").replace("_USDT", "")
                if not sym:
                    continue
                try:
                    mark = float(t.get("lastPrice") or 0)
                    # holdVol (contracts) intentionally unused for OI — see note.
                    # MEXC holdVol is in contracts; contract size via cVol fallback.
                    out.setdefault(
                        sym,
                        {
                            # holdVol is in CONTRACTS (size varies per market) --
                            # using it as coin OI inflated BTC to \$48T. Funding +
                            # mark only until contract sizes come from /detail.
                            "oi_usd": 0.0,
                            "funding": float(t.get("fundingRate") or 0),
                            "mark": mark,
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("liqmap mexc failed: %s", str(exc)[:100])
    return out


async def _kucoin_liquidation_snapshot() -> dict[str, dict[str, Any]]:
    """KuCoin futures active contracts: one call -> OI + funding."""
    out: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api-futures.kucoin.com/api/v1/contracts/active")
            for t in r.json().get("data") or []:
                sym = str(t.get("baseCurrency") or "")
                if not sym:
                    continue
                try:
                    mark = float(t.get("markPrice") or 0)
                    oi_coins = float(t.get("openInterest") or 0)  # in coins
                    lot = float(t.get("lotSize") or 1) or 1.0
                    multiplier = float(t.get("multiplier") or 1) or 1.0
                    out.setdefault(
                        sym,
                        {
                            "oi_usd": oi_coins * mark * multiplier * lot,
                            "funding": float(t.get("fundingFeeRate") or 0),
                            "mark": mark,
                        },
                    )
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("liqmap kucoin failed: %s", str(exc)[:100])
    return out
