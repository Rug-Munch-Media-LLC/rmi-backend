"""
Market Intelligence - Scam Detection & Alerts Feed Routes
Scam alerts from DexScreener suspicious tokens and combined alerts feed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.logging import get_logger
from app.routers.market_intel.providers import _cg, _ds
from app.routers.market_intel.utils import _now, _safe_float

logger = get_logger(__name__)
router = APIRouter(tags=["market-intel"])


@router.get("/scams")
async def get_market_scams(
    request: Request,
    limit: int = Query(5, ge=1, le=50),
    tier: str = Query("free"),
) -> dict[str, Any]:
    """Scam alerts - flagged tokens from DexScreener with suspicious patterns."""
    now = _now()
    items = []
    boosts = await _ds("/token-boosts/latest/v1", ttl=60)
    if boosts and isinstance(boosts, list):
        trending = await _cg("/search/trending", ttl=120)
        trending_symbols = set()
        if trending and "coins" in trending:
            for c in trending["coins"]:
                trending_symbols.add(c.get("item", {}).get("symbol", "").upper())

        for b in boosts[:20]:
            addr = b.get("tokenAddress", "")
            chain = b.get("chainId", "solana")
            pairs_data = await _ds(f"/latest/dex/search?q={addr}", ttl=120)
            price = 0
            mcap = 0
            vol_24h = 0
            change_24h = 0
            name = b.get("description", "")[:30] if b.get("description") else ""
            symbol = addr[:6].upper()
            liquidity = 0
            if pairs_data and isinstance(pairs_data, dict) and pairs_data.get("pairs"):
                p = pairs_data["pairs"][0]
                bt = p.get("baseToken", {})
                symbol = bt.get("symbol", symbol).upper()
                name = bt.get("name", name)
                price = _safe_float(p.get("priceUsd"))
                mcap = _safe_float(p.get("fdv"))
                liquidity = _safe_float(
                    p.get("liquidity", {}).get("usd") if isinstance(p.get("liquidity"), dict) else 0
                )
                vol_24h = _safe_float((p.get("volume") or {}).get("h24") if isinstance(p.get("volume"), dict) else 0)
                change_24h = _safe_float(
                    (p.get("priceChange") or {}).get("h24") if isinstance(p.get("priceChange"), dict) else 0
                )

            if (
                abs(change_24h) > 300
                or (mcap > 0 and liquidity > 0 and liquidity / mcap < 0.01)
                or (price > 0 and price < 0.00001 and vol_24h > 100000)
            ):
                scam_type = "rug_pull" if change_24h > 500 else "honeypot" if price < 0.0001 else "copycat"
                severity = "critical" if change_24h > 500 else "high" if change_24h > 300 else "medium"
                indicators = []
                if change_24h > 300:
                    indicators.append(f"+{change_24h:.0f}% 24h pump")
                if liquidity > 0 and mcap > 0 and liquidity / mcap < 0.01:
                    indicators.append(f"Low liquidity ratio ({liquidity / mcap * 100:.1f}%)")
                if mcap > 0 and mcap < 50000:
                    indicators.append("Micro-cap (<$50K)")
                if not indicators:
                    indicators.append("Suspicious trading pattern")

                items.append(
                    {
                        "id": f"scam_{chain}_{addr[:12]}",
                        "token_address": addr,
                        "symbol": symbol,
                        "name": name or symbol,
                        "chain": chain,
                        "type": scam_type,
                        "severity": severity,
                        "status": "active",
                        "victims": int(vol_24h / 100) if vol_24h > 0 else 0,
                        "amount_stolen": vol_24h * 0.3 if vol_24h > 0 else 0,
                        "detected_at": now,
                        "description": f"{symbol} showing {change_24h:+.0f}% 24h change with ${liquidity:,.0f} liquidity. {'Extreme pump detected.' if change_24h > 500 else 'Suspicious pattern flagged.'}",
                        "indicators": indicators[:4],
                    }
                )

    items = items[:limit]
    return {"items": items, "total": len(items), "updated_at": now}


@router.get("/alerts-feed")
async def get_market_alerts_feed(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    tier: str = Query("free"),
) -> dict[str, Any]:
    """Combined alerts feed - scams from DexScreener, system alerts, bad actors."""
    now = _now()
    items = []

    # 1. Scam alerts from DexScreener suspicious tokens
    boosts = await _ds("/token-boosts/latest/v1", ttl=60)
    if boosts and isinstance(boosts, list):
        for b in boosts[:30]:
            addr = b.get("tokenAddress", "")
            chain = b.get("chainId", "solana")
            pairs_data = await _ds(f"/latest/dex/search?q={addr}", ttl=120)
            price = 0
            mcap = 0
            vol_24h = 0
            change_24h = 0
            liquidity = 0
            symbol = addr[:6].upper()
            if pairs_data and isinstance(pairs_data, dict) and pairs_data.get("pairs"):
                p = pairs_data["pairs"][0]
                bt = p.get("baseToken", {})
                symbol = bt.get("symbol", symbol).upper()
                bt.get("name", "")
                price = _safe_float(p.get("priceUsd"))
                mcap = _safe_float(p.get("fdv"))
                liquidity = _safe_float(
                    p.get("liquidity", {}).get("usd") if isinstance(p.get("liquidity"), dict) else 0
                )
                vol_24h = _safe_float((p.get("volume") or {}).get("h24") if isinstance(p.get("volume"), dict) else 0)
                change_24h = _safe_float(
                    (p.get("priceChange") or {}).get("h24") if isinstance(p.get("priceChange"), dict) else 0
                )

            if abs(change_24h) > 200 or (mcap > 0 and liquidity > 0 and liquidity / mcap < 0.02):
                if change_24h > 500:
                    alert_type = "pump_dump"
                    severity = "critical"
                    desc = f"{symbol} pumped {change_24h:+.0f}% in 24h. Classic pump-and-dump pattern with ${liquidity:,.0f} liquidity."
                elif change_24h < -80:
                    alert_type = "rug_pull"
                    severity = "critical"
                    desc = f"{symbol} crashed {change_24h:.0f}% in 24h. Likely rug pull or honeypot."
                elif mcap > 0 and liquidity / mcap < 0.02:
                    alert_type = "low_liquidity"
                    severity = "high"
                    desc = f"{symbol} has only {liquidity / mcap * 100:.1f}% liquidity ratio. Exit liquidity trap."
                elif price > 0 and price < 0.00001 and vol_24h > 50000:
                    alert_type = "shill_campaign"
                    severity = "medium"
                    desc = f"{symbol} micro-cap token with high volume relative to price. Likely shill campaign."
                else:
                    alert_type = "suspicious"
                    severity = "medium"
                    desc = f"{symbol} showing anomalous trading. {change_24h:+.0f}% 24h, ${liquidity:,.0f} liq."

                items.append(
                    {
                        "id": f"scam_{chain}_{addr[:12]}",
                        "type": alert_type,
                        "title": f"{alert_type.replace('_', ' ').title()}: {symbol} on {chain.title()}",
                        "description": desc,
                        "severity": severity,
                        "token": symbol,
                        "chain": chain,
                        "wallet": addr[:16] + "...",
                        "amount_usd": vol_24h,
                        "created_at": now,
                        "source": "dexscreener",
                        "metadata": {
                            "price": price,
                            "change_24h": change_24h,
                            "liquidity": liquidity,
                            "market_cap": mcap,
                        },
                    }
                )

    # 2. System alerts from the existing alerts endpoint (internal fetch)
    try:
        import httpx as _hx

        base_url = str(request.base_url).rstrip("/")
        async with _hx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{base_url}/api/v1/alerts")
            if r.status_code == 200:
                sys_data = r.json()
                for a in (sys_data.get("alerts") or [])[:15]:
                    items.append(
                        {
                            "id": a.get("id", ""),
                            "type": a.get("type", "alert"),
                            "title": a.get("title", ""),
                            "description": a.get("description", ""),
                            "severity": a.get("severity", "medium"),
                            "token": a.get("token") or "",
                            "chain": a.get("chain") or "",
                            "wallet": a.get("wallet") or "",
                            "amount_usd": _safe_float(a.get("amount_usd", 0)),
                            "created_at": a.get("created_at", now),
                            "source": "rmi_detector",
                            "metadata": a.get("metadata", {}),
                        }
                    )
    except Exception as e:
        logger.warning(f"Failed to fetch system alerts: {e}")

    # 3. Bad actors from rugmaps
    try:
        async with _hx.AsyncClient(timeout=5) as c:
            r2 = await c.get(f"{base_url}/api/v1/rugmaps/bad-actors")
            if r2.status_code == 200:
                ba_data = r2.json()
                for a in (ba_data.get("bad_actors") or [])[:10]:
                    items.append(
                        {
                            "id": f"bad_actor_{a.get('address', '')[:12]}",
                            "type": "bad_actor",
                            "title": f"Known Bad Actor: {a.get('label', 'Unknown')}",
                            "description": a.get(
                                "description", f"Flagged wallet {a.get('address', '')[:16]}... - {a.get('label', '')}"
                            ),
                            "severity": a.get("severity", "high"),
                            "token": "",
                            "chain": a.get("chain", ""),
                            "wallet": a.get("address", ""),
                            "amount_usd": 0,
                            "created_at": now,
                            "source": "rugmaps",
                            "metadata": {"label": a.get("label", ""), "tags": a.get("tags", [])},
                        }
                    )
    except Exception as e:
        logger.warning(f"Failed to fetch bad actors: {e}")

    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    items.sort(key=lambda x: (sev_order.get(x.get("severity", "low"), 4), x.get("created_at", now)), reverse=False)
    items = items[:limit]
    return {"items": items, "total": len(items), "updated_at": now}
