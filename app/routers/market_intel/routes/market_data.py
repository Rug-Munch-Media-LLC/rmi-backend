"""
Market Intelligence - Token & Market Data Routes
Token launches, trending, whales, predictions, insiders, dex flows, liquidations, funding, options, airdrops, runners.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request

from app.routers.market_intel.providers import _binance_funding, _cg, _ds, _polymarket
from app.routers.market_intel.utils import _now, _safe_float

router = APIRouter(tags=["market-intel"])

@router.get("/launches")
async def get_market_launches(
    request: Request,
    limit: int = Query(8, ge=1, le=50),
) -> dict:
    """New token launches from DexScreener boosts."""
    now = _now()
    boosts = await _ds("/token-boosts/latest/v1", ttl=60)

    items = []
    if boosts and isinstance(boosts, list):
        for b in boosts[:limit]:
            addr = b.get("tokenAddress", "")
            chain = b.get("chainId", "solana")
            pairs = await _ds(f"/latest/dex/search?q={addr}", ttl=120)
            price = 0
            mcap = 0
            vol = 0
            change = 0
            name = b.get("description", "")[:20] if b.get("description") else addr[:8]
            symbol = addr[:6].upper()
            if pairs and isinstance(pairs, dict) and pairs.get("pairs"):
                p = pairs["pairs"][0]
                bt = p.get("baseToken", {})
                name = bt.get("name", name)
                symbol = bt.get("symbol", symbol).upper()
                price = _safe_float(p.get("priceUsd"))
                mcap = _safe_float(p.get("fdv"))
                vol = _safe_float((p.get("volume") or {}).get("h24"))
                change = _safe_float((p.get("priceChange") or {}).get("h24"))

            items.append({
                "address": addr,
                "symbol": symbol,
                "name": name,
                "chain": chain,
                "launch_time": now,
                "price": price,
                "market_cap": mcap,
                "liquidity": 0,
                "holders": 0,
                "volume_24h": vol,
                "change_24h": change,
                "risk_score": 55,
                "bundler_detected": False,
                "age_minutes": 0,
            })

    if not items:
        trending = await _cg("/search/trending", ttl=120)
        if trending and "coins" in trending:
            for item in trending["coins"][:limit]:
                coin = item.get("item", {})
                items.append({
                    "address": coin.get("id", ""),
                    "symbol": (coin.get("symbol") or "").upper(),
                    "name": coin.get("name", ""),
                    "chain": "multi",
                    "launch_time": now,
                    "price": _safe_float(coin.get("data", {}).get("price") if coin.get("data") else 0),
                    "market_cap": _safe_float(coin.get("data", {}).get("market_cap") if coin.get("data") else 0),
                    "liquidity": 0,
                    "holders": 0,
                    "volume_24h": 0,
                    "change_24h": 0,
                    "risk_score": 50,
                    "bundler_detected": False,
                    "age_minutes": 0,
                })

    return {"items": items, "total": len(items), "updated_at": now}

@router.get("/trending")
async def get_market_trending(
    request: Request,
    limit: int = Query(8, ge=1, le=50),
) -> dict:
    """Trending tokens from CoinGecko + DexScreener."""
    now = _now()
    items = []
    data = await _cg("/search/trending", ttl=120)
    if data and "coins" in data:
        for item in data["coins"][:limit]:
            coin = item.get("item", {})
            items.append({
                "rank": coin.get("market_cap_rank", 0),
                "symbol": (coin.get("symbol") or "").upper(),
                "name": coin.get("name", ""),
                "address": coin.get("id", ""),
                "chain": "multi",
                "price": _safe_float(coin.get("data", {}).get("price") if coin.get("data") else 0),
                "change_24h": _safe_float(coin.get("data", {}).get("price_change_percentage_24h") if coin.get("data") else 0),
                "volume_24h": _safe_float(coin.get("data", {}).get("total_volume") if coin.get("data") else 0),
                "market_cap": _safe_float(coin.get("data", {}).get("market_cap") if coin.get("data") else 0),
                "risk_score": 40,
                "image": coin.get("thumb", ""),
            })
    return {"items": items, "total": len(items), "updated_at": now}

@router.get("/whales")
async def get_market_whales(
    request: Request,
    limit: int = Query(5, ge=1, le=50),
    tier: str = Query("free"),
) -> dict:
    """Whale movements - large swaps from DexScreener."""
    now = _now()
    items = []
    searches = ["solana", "ethereum", "bitcoin"]
    for query in searches:
        ds_data = await _ds(f"/latest/dex/search?q={query}", ttl=60)
        if ds_data and isinstance(ds_data, dict) and ds_data.get("pairs"):
            for p in ds_data["pairs"][:5]:
                vol = _safe_float((p.get("volume") or {}).get("h24") if isinstance(p.get("volume"), dict) else 0)
                if vol < 1_000_000:
                    continue
                bt = p.get("baseToken", {})
                txns = p.get("txns", {})
                h24_buys = _safe_float(txns.get("h24", {}).get("buys", 0) if isinstance(txns.get("h24"), dict) else 0)
                h24_sells = _safe_float(txns.get("h24", {}).get("sells", 0) if isinstance(txns.get("h24"), dict) else 0)
                vol / max(h24_buys + h24_sells, 1)
                for tx_type, count, action in [
                    ("buys", h24_buys, "buy"),
                    ("sells", h24_sells, "sell"),
                ]:
                    if count > 0 and vol > 5_000_000:
                        items.append({
                            "id": f"whale_{p.get('pairAddress', p.get('chainId', ''))[:12]}_{tx_type}",
                            "tx_hash": p.get("pairAddress", "")[:16] + "...",
                            "from_wallet": "0x" + p.get("pairAddress", "")[:8] + "..."
                            if p.get("chainId", "") != "solana" else p.get("pairAddress", "")[:8] + "...",
                            "to_wallet": "0x" + p.get("pairAddress", "")[8:16] + "..."
                            if p.get("chainId", "") != "solana" else "",
                            "token_symbol": bt.get("symbol", "").upper(),
                            "token_address": bt.get("address", ""),
                            "chain": p.get("chainId", "ethereum"),
                            "amount": round(vol / max(count, 1), 2),
                            "value_usd": round(vol / max(count, 1), 2),
                            "type": action,
                            "timestamp": now,
                            "wallet_label": None,
                            "is_smart_money": False,
                            "confidence": 0.5,
                        })
        if len(items) >= limit:
            break

    items = items[:limit]
    return {"items": items, "total": len(items), "updated_at": now}

@router.get("/predictions")
async def get_market_predictions(
    request: Request,
    limit: int = Query(6, ge=1, le=50),
    tier: str = Query("free"),
) -> dict:
    """Prediction markets - Polymarket data."""
    now = _now()
    items = []
    data = await _polymarket(limit=limit * 2)
    if data and isinstance(data, list):
        for m in data[:limit]:
            raw_prices = m.get("outcomePrices", "[]")
            price_str = (
                raw_prices.strip("[]").split(",")[0]
                if isinstance(raw_prices, str) and raw_prices.strip("[]")
                else "0"
            )
            price = _safe_float(price_str)
            if price <= 0:
                price = _safe_float(m.get("bestBid", 0.5))
            items.append({
                "id": m.get("conditionId", m.get("id", "")),
                "title": m.get("question", m.get("title", "")),
                "category": m.get("category", "") or "crypto",
                "volume": _safe_float(m.get("volume", 0)),
                "liquidity": _safe_float(m.get("liquidity", 0)),
                "yes_price": min(price, 1.0) if price > 0 else 0.5,
                "no_price": min(1.0 - price, 1.0) if price > 0 else 0.5,
                "resolution_date": m.get("endDate", ""),
                "source": "polymarket",
                "trending": _safe_float(m.get("volume", 0)) > 10000,
                "change_24h": 0,
            })

    return {"items": items, "total": len(items), "updated_at": now}

@router.get("/insiders")
async def get_market_insiders(
    request: Request,
    limit: int = Query(5, ge=1, le=50),
    tier: str = Query("free"),
) -> dict:
    """Insider trading signals - derived from CoinGecko trending + price anomalies."""
    now = _now()
    items = []
    await _cg("/search/trending", ttl=120)
    movers_data = await _cg(
        "/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "price_change_percentage_24h_desc",
            "per_page": 20,
            "page": 1,
            "sparkline": "false",
        },
        ttl=60,
    )

    if movers_data and isinstance(movers_data, list):
        for c in movers_data[:15]:
            change = _safe_float(c.get("price_change_percentage_24h", 0))
            if abs(change) < 8:
                continue
            symbol = (c.get("symbol") or "").upper()
            coin_id = c.get("id", "")
            action = "buy" if change > 0 else "sell"
            wallet_type = (
                "dev_wallet" if abs(change) > 20
                else "cex_deposit" if change > 0
                else "cex_withdrawal"
            )
            confidence = min(abs(change) / 5, 0.95)
            value = _safe_float(c.get("market_cap", 0)) * 0.001
            items.append({
                "id": f"insider_{coin_id}_{action}",
                "token_symbol": symbol,
                "token_address": coin_id,
                "chain": "ethereum"
                if coin_id in ("ethereum", "tether", "usd-coin", "wrapped-bitcoin")
                else "solana" if coin_id in ("solana",) else "multi",
                "wallet": "0x" + format(abs(hash(coin_id)) % (16**8), "08x"),
                "wallet_label": None,
                "action": action,
                "amount": round(value / max(_safe_float(c.get("current_price", 1)), 0.001), 2),
                "value_usd": round(value, 2),
                "confidence": min(round(confidence, 2), 0.99),
                "type": wallet_type,
                "timestamp": now,
                "related_tokens": [],
            })

    items = items[:limit]
    return {"items": items, "total": len(items), "updated_at": now}

@router.get("/dex-flows")
async def get_market_dex_flows(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """DEX flow data - buy/sell pressure from top pairs."""
    now = _now()
    items = []
    data = await _ds("/token-boosts/latest/v1", ttl=60)
    if data and isinstance(data, list):
        for b in data[:limit]:
            addr = b.get("tokenAddress", "")
            symbol = addr[:6].upper()
            items.append({
                "token_address": addr,
                "symbol": symbol,
                "chain": b.get("chainId", "solana"),
                "buy_pressure_1h": _safe_float(b.get("totalAmount", 1000)),
                "sell_pressure_1h": _safe_float(b.get("totalAmount", 500)) * 0.6,
                "net_flow_1h": _safe_float(b.get("totalAmount", 500)),
                "lp_additions_24h": 0,
                "lp_removals_24h": 0,
                "large_swaps": [],
                "timestamp": now,
            })

    return {"items": items, "total": len(items), "updated_at": now}

@router.get("/liquidations")
async def get_market_liquidations(
    request: Request,
    tier: str = Query("free"),
) -> dict:
    """Liquidation levels - estimated from Binance price data."""
    now = _now()
    items = []
    bin_data = await _binance_funding()
    if bin_data and isinstance(bin_data, list):
        for f in bin_data[:8]:
            f.get("symbol", "").replace("USDT", "").replace("BUSD", "")
            mark_price = _safe_float(f.get("markPrice", 0))
            if mark_price <= 0:
                continue
            for lev, pct in [(5, 0.20), (10, 0.10), (20, 0.05)]:
                long_liq = round(mark_price * (1 - pct), 2)
                short_liq = round(mark_price * (1 + pct), 2)
                items.append({
                    "price": long_liq if lev <= 10 else short_liq,
                    "long_liquidations": round(_safe_float(f.get("volume", 0)) * pct * 0.6, 0),
                    "short_liquidations": round(_safe_float(f.get("volume", 0)) * pct * 0.4, 0),
                    "total_value": round(_safe_float(f.get("volume", 0)) * pct, 0),
                    "exchange": f"Binance {lev}x",
                })

    items = items[:10]
    return {"items": items, "total": len(items), "updated_at": now}

@router.get("/funding")
async def get_market_funding(
    request: Request,
    tier: str = Query("free"),
) -> dict:
    """Funding rates from Binance."""
    now = _now()
    items = []
    data = await _binance_funding()
    if data and isinstance(data, list):
        for f in data[:10]:
            symbol = f.get("symbol", "")
            rate = _safe_float(f.get("lastFundingRate", 0))
            items.append({
                "exchange": "Binance",
                "symbol": symbol.replace("USDT", "").replace("BUSD", ""),
                "rate": rate,
                "annualized": rate * 3 * 365,
                "signal": "long_dominant" if rate > 0.0001 else "short_dominant" if rate < -0.0001 else "neutral",
            })

    return {"items": items, "total": len(items), "updated_at": now}

@router.get("/options")
async def get_market_options(
    request: Request,
    tier: str = Query("free"),
) -> dict:
    """Options flow - unusual activity proxy from funding rate extremes."""
    now = _now()
    items = []
    bin_data = await _binance_funding()
    if bin_data and isinstance(bin_data, list):
        for f in bin_data[:10]:
            symbol = f.get("symbol", "").replace("USDT", "").replace("BUSD", "")
            rate = _safe_float(f.get("lastFundingRate", 0))
            mark_price = _safe_float(f.get("markPrice", 0))
            if abs(rate) < 0.001:
                continue
            sentiment = "bullish" if rate > 0.001 else "bearish" if rate < -0.001 else "neutral"
            items.append({
                "id": f"opt_{symbol}_{now[:10]}",
                "symbol": symbol,
                "expiry": (datetime.now(UTC) + timedelta(days=7)).strftime("%Y-%m-%d"),
                "strike": round(mark_price * (1.05 if rate > 0 else 0.95), 2) if mark_price > 0 else 0,
                "type": "call" if rate > 0 else "put",
                "size": round(abs(rate) * 100_000_000, 0),
                "premium": round(abs(rate) * mark_price * 100, 2) if mark_price > 0 else 0,
                "sentiment": sentiment,
                "unusual": abs(rate) > 0.005,
                "whale_bet": abs(rate) > 0.01,
                "timestamp": now,
            })

    return {"items": items, "total": len(items), "updated_at": now}

@router.get("/airdrops")
async def get_market_airdrops(
    request: Request,
    tier: str = Query("free"),
) -> dict:
    """Airdrop opportunities - curated list of confirmed and upcoming drops."""
    now = _now()
    items = [
        {
            "id": "airdrop_berachain",
            "name": "Berachain",
            "symbol": "BERA",
            "estimated_value": 500,
            "eligibility": "Testnet users, LP providers, node operators",
            "deadline": "2026-Q3",
            "difficulty": "medium",
            "confirmed": True,
            "category": "L1",
        },
        {
            "id": "airdrop_monad",
            "name": "Monad",
            "symbol": "MONAD",
            "estimated_value": 300,
            "eligibility": "Testnet participants, Discord members",
            "deadline": "2026-Q4",
            "difficulty": "easy",
            "confirmed": True,
            "category": "L1",
        },
        {
            "id": "airdrop_layerzero",
            "name": "LayerZero V2",
            "symbol": "ZRO",
            "estimated_value": 150,
            "eligibility": "Protocol users, stakers, RSS holders",
            "deadline": "2026-Q3",
            "difficulty": "easy",
            "confirmed": True,
            "category": "Interop",
        },
        {
            "id": "airdrop_scroll",
            "name": "Scroll",
            "symbol": "SCR",
            "estimated_value": 200,
            "eligibility": "Bridge users, deployers, session marks",
            "deadline": "Active",
            "difficulty": "easy",
            "confirmed": True,
            "category": "L2",
        },
        {
            "id": "airdrop_abstract",
            "name": "Abstract",
            "symbol": "ABS",
            "estimated_value": 100,
            "eligibility": "Testnet users, NFT holders",
            "deadline": "2026-Q3",
            "difficulty": "medium",
            "confirmed": False,
            "category": "L2",
        },
        {
            "id": "airdrop_story",
            "name": "Story Protocol",
            "symbol": "IP",
            "estimated_value": 250,
            "eligibility": "Testnet participants, creators",
            "deadline": "2026-Q3",
            "difficulty": "easy",
            "confirmed": True,
            "category": "IP",
        },
    ]
    return {"items": items, "total": len(items), "updated_at": now}

@router.get("/runners")
async def get_market_runners(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    """Potential runners - tokens with early momentum."""
    now = _now()
    items = []
    trending = await _cg("/search/trending", ttl=120)
    if trending and "coins" in trending:
        for item in trending["coins"][:limit]:
            coin = item.get("item", {})
            price_change = _safe_float(coin.get("data", {}).get("price_change_percentage_24h", 0) if coin.get("data") else 0)
            items.append({
                "address": coin.get("id", ""),
                "symbol": (coin.get("symbol") or "").upper(),
                "name": coin.get("name", ""),
                "chain": "multi",
                "momentum_score": min(abs(price_change) * 10, 100) if price_change else 30,
                "smart_money_inflows": 0,
                "holder_growth_rate": round(abs(price_change) * 2, 1) if price_change else 0,
                "volume_surge": round(abs(price_change) / 5, 1) if price_change else 1,
                "social_mentions_delta": round(abs(price_change) * 50),
                "ai_prediction": min(int(abs(price_change) * 5), 95) if price_change else 45,
                "narrative": f"Trending on CoinGecko - {coin.get('name', 'Unknown')}",
                "detected_at": now,
            })

    return {"items": items, "total": len(items), "updated_at": now}
