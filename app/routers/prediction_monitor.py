"""Prediction Market Monitor — Polymarket + Kalshi + Manifold tracker.
Premium feature: wallet-level tracking, insider pattern detection, P&L analytics."""

import json
import os
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/prediction-markets", tags=["prediction-markets"])

POLYMARKET = "https://clob.polymarket.com"
BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
RMI_KEY = os.getenv("RMI_INTERNAL_KEY", "rmi-internal-2026")

# Tracked wallet registry (Redis in prod, in-memory for now)
_tracked_wallets: dict[str, dict] = {}
_insider_flags: list[dict] = []

# ═══════════════════════════════════════════
# MARKET DATA
# ═══════════════════════════════════════════


@router.get("/markets")
async def get_markets(
    category: str = Query("crypto", description="crypto|politics|sports|all"), limit: int = Query(10, le=50)
):
    """Get active prediction markets from Polymarket."""
    markets = []
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{POLYMARKET}/markets", params={"limit": limit, "closed": "false"})
            if r.status_code == 200:
                data = r.json()
                for m in data if isinstance(data, list) else data.get("data", []):
                    if category == "all" or category in (m.get("category", "") or "").lower():
                        markets.append(
                            {
                                "id": m.get("id"),
                                "question": m.get("question"),
                                "volume_usd": m.get("volumeNum", 0),
                                "liquidity": m.get("liquidityNum", 0),
                                "end_date": m.get("endDateIso"),
                                "outcomes": m.get("outcomes", []),
                                "outcome_prices": json.loads(m.get("outcomePrices", "[]")),
                                "category": m.get("category"),
                            }
                        )
    except Exception:
        pass

    # Also get from DataBus if available
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{BACKEND}/api/v1/databus/fetch/prediction_markets", headers={"X-RMI-Key": RMI_KEY})
            if r.status_code == 200:
                db_markets = r.json().get("data", r.json()).get("markets", [])
                markets.extend(db_markets)
    except Exception:
        pass

    markets.sort(key=lambda m: m.get("volume_usd", 0), reverse=True)
    return {"markets": markets[:limit], "source": "polymarket+databus", "count": len(markets[:limit])}


@router.get("/market/{market_id}")
async def get_market_detail(market_id: str):
    """Get detailed market info + recent trades."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{POLYMARKET}/markets/{market_id}")
            if r.status_code == 200:
                market = r.json()
                return {
                    "id": market.get("id"),
                    "question": market.get("question"),
                    "description": market.get("description"),
                    "volume_usd": market.get("volumeNum", 0),
                    "liquidity": market.get("liquidityNum", 0),
                    "end_date": market.get("endDateIso"),
                    "outcomes": market.get("outcomes", []),
                    "outcome_prices": json.loads(market.get("outcomePrices", "[]")),
                }
    except Exception:
        pass
    return {"error": "Market not found"}


# ═══════════════════════════════════════════
# WALLET TRACKING (PREMIUM)
# ═══════════════════════════════════════════


@router.post("/track-wallet")
async def track_wallet(wallet_address: str, label: str = "", user_id: str = ""):
    """Start tracking a wallet's prediction market activity. Premium feature."""
    wid = f"{user_id}:{wallet_address}" if user_id else wallet_address
    _tracked_wallets[wid] = {
        "wallet": wallet_address,
        "label": label,
        "user_id": user_id,
        "added_at": datetime.now(UTC).isoformat(),
        "positions": [],
        "pnl_usd": 0,
        "win_rate": 0,
        "total_trades": 0,
    }
    return {"tracked": wid, "wallet": wallet_address, "status": "monitoring"}


@router.get("/wallet/{wallet_address}")
async def get_wallet_profile(wallet_address: str):
    """Get a tracked wallet's prediction market profile."""
    # Find wallet in tracked registry
    wallet_data = None
    for wid, data in _tracked_wallets.items():
        if wallet_address in wid:
            wallet_data = data
            break

    if not wallet_data:
        # Try to fetch from DataBus
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{BACKEND}/api/v1/databus/fetch/entity_intel?address={wallet_address}",
                    headers={"X-RMI-Key": RMI_KEY},
                )
                if r.status_code == 200:
                    wallet_data = r.json().get("data", r.json())
        except Exception:
            pass

    if not wallet_data:
        return {
            "wallet": wallet_address,
            "tracked": False,
            "note": "Not tracked. Use POST /api/v1/prediction-markets/track-wallet",
        }

    # Check for insider patterns
    insider_risk = _analyze_insider_patterns(wallet_address, wallet_data)

    return {
        "wallet": wallet_address,
        "tracked": True,
        "label": wallet_data.get("label", ""),
        "positions": wallet_data.get("positions", []),
        "pnl_usd": wallet_data.get("pnl_usd", 0),
        "win_rate": wallet_data.get("win_rate", 0),
        "total_trades": wallet_data.get("total_trades", 0),
        "insider_risk": insider_risk,
    }


# ═══════════════════════════════════════════
# INSIDER DETECTION (PREMIUM)
# ═══════════════════════════════════════════


def _analyze_insider_patterns(wallet: str, data: dict) -> dict:
    """Analyze wallet for insider trading patterns."""
    risk = 0
    flags = []

    trades = data.get("positions", [])
    if not trades:
        return {"risk": "unknown", "score": 0, "flags": []}

    # Pattern 1: Trades right before major price moves
    pre_move_trades = 0
    for trade in trades:
        price_before = trade.get("price_before_move", trade.get("avg_price", 0))
        price_after = trade.get("price_after_move", trade.get("current_price", 0))
        if price_before > 0 and abs(price_after - price_before) / price_before > 0.3:
            pre_move_trades += 1

    if pre_move_trades > 3:
        risk += 30
        flags.append(f"{pre_move_trades} trades before >30% price moves")

    # Pattern 2: High win rate (>80%) with significant volume
    win_rate = data.get("win_rate", 0)
    total_trades = data.get("total_trades", 0)
    volume = data.get("pnl_usd", 0)

    if win_rate > 80 and total_trades > 20 and volume > 10000:
        risk += 25
        flags.append(f"{win_rate}% win rate across {total_trades} trades — statistically anomalous")

    # Pattern 3: Trades on markets with low public interest
    low_interest_trades = sum(1 for t in trades if t.get("market_volume", 0) < 1000)
    if low_interest_trades > 5 and win_rate > 70:
        risk += 20
        flags.append(f"{low_interest_trades} trades on low-volume markets with {win_rate}% win rate")

    # Pattern 4: Consistent last-minute entries before resolution
    last_minute = sum(1 for t in trades if t.get("hours_before_resolution", 999) < 1)
    if last_minute > 3:
        risk += 15
        flags.append(f"{last_minute} trades within 1 hour of resolution — possible info leak")

    if risk >= 60:
        level = "CRITICAL"
    elif risk >= 35:
        level = "HIGH"
    elif risk >= 15:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {"risk": level, "score": min(100, risk), "flags": flags}


@router.get("/insider-leaderboard")
async def insider_leaderboard(min_risk: int = Query(35, le=100), limit: int = Query(10, le=25)):
    """Get wallets flagged for potential insider trading. Premium feature."""
    flagged = []
    for wid, data in list(_tracked_wallets.items())[:50]:
        analysis = _analyze_insider_patterns(wid, data)
        if analysis["score"] >= min_risk:
            flagged.append(
                {
                    "wallet": data["wallet"],
                    "label": data.get("label", "Unlabeled"),
                    "insider_risk": analysis["risk"],
                    "risk_score": analysis["score"],
                    "flags": analysis["flags"],
                    "pnl_usd": data.get("pnl_usd", 0),
                    "win_rate": data.get("win_rate", 0),
                }
            )

    flagged.sort(key=lambda w: w["risk_score"], reverse=True)
    return {"flagged_wallets": flagged[:limit], "total_flagged": len(flagged)}


@router.get("/market-snapshot")
async def market_snapshot():
    """Quick overview of prediction markets + insider activity."""
    markets = (await get_markets("all", 5))["markets"]
    top_markets = [{"question": m["question"][:80], "volume_usd": m["volume_usd"]} for m in markets[:5]]

    insider_count = len([w for w in _tracked_wallets.values() if w.get("insider_flag", False)])

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "active_markets": len(markets),
        "top_markets": top_markets,
        "tracked_wallets": len(_tracked_wallets),
        "insider_flags": insider_count,
        "source": "Polymarket + DataBus",
    }
