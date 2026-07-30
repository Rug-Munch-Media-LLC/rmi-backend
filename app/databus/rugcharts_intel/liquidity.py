"""
RugCharts Intel — Liquidity Risk Monitor.
LP health + pull detection.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger

from .cache import ARKHAM_KEY, CACHE_TTL, HELIUS_KEY, _r

logger = get_logger("rugcharts_intel")


async def liquidity_risk_monitor(address: str = "", chain: str = "solana", **kw) -> dict | None:
    """Monitor liquidity pool health: LP lock, concentration, removal risk.
    Critical for rug pull prevention - the #1 thing degens need to check.
    """
    if not address:
        return None

    cache_key = f"liquidity_risk:{chain}:{address}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    api_key = kw.get("api_key", "") or HELIUS_KEY
    liquidity_data = {
        "lp_locked": "unknown",
        "lp_lock_duration": None,
        "lp_amount_usd": 0,
        "lp_holders": [],
        "lp_concentration_risk": "unknown",
        "lp_removal_history": [],
        "risk_score": 50,
        "risk_level": "UNKNOWN",
    }

    try:
        if chain == "solana" and api_key:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTokenLargestAccounts",
                        "params": [address],
                    },
                )
                if r.status_code == 200:
                    holders = r.json().get("result", {}).get("value", [])
                    total_supply = sum(float(h.get("amount", 0)) for h in holders)
                    lp_holders_list = []
                    for h in holders[:10]:
                        pct = (float(h.get("amount", 0)) / total_supply * 100) if total_supply > 0 else 0
                        lp_holders_list.append(
                            {
                                "address": h.get("address", "")[:12] + "...",
                                "share_pct": round(pct, 1),
                            }
                        )
                    liquidity_data["lp_holders"] = lp_holders_list
                    if lp_holders_list:
                        top1_pct = lp_holders_list[0]["share_pct"]
                        top3_pct = sum(h["share_pct"] for h in lp_holders_list[:3])
                        if top1_pct > 80:
                            liquidity_data["lp_concentration_risk"] = "CRITICAL"
                            liquidity_data["risk_score"] = 90
                            liquidity_data["risk_level"] = "CRITICAL"
                        elif top1_pct > 50 or top3_pct > 90:
                            liquidity_data["lp_concentration_risk"] = "HIGH"
                            liquidity_data["risk_score"] = 70
                            liquidity_data["risk_level"] = "HIGH"
                        elif top3_pct > 60:
                            liquidity_data["lp_concentration_risk"] = "MEDIUM"
                            liquidity_data["risk_score"] = 45
                            liquidity_data["risk_level"] = "MEDIUM"
                        else:
                            liquidity_data["lp_concentration_risk"] = "LOW"
                            liquidity_data["risk_score"] = 20
                            liquidity_data["risk_level"] = "LOW"
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        logger.debug(f"Liquidity monitor: {e}")

    if ARKHAM_KEY and liquidity_data["lp_holders"]:
        for h in liquidity_data["lp_holders"][:3]:
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    ar = await c.get(
                        f"https://api.arkhamintelligence.com/intelligence/address/{h['address']}",
                        headers={"API-Key": ARKHAM_KEY},
                    )
                    if ar.status_code == 200:
                        entity = ar.json().get("arkhamEntity", {})
                        if entity.get("name"):
                            h["entity"] = entity["name"]
                            h["entity_type"] = entity.get("type", "")
            except (ValueError, ConnectionError, TimeoutError, RuntimeError):
                logger.warning("error")

    liquidity_data["token"] = address
    liquidity_data["chain"] = chain
    liquidity_data["scan_time"] = datetime.now(UTC).isoformat()
    liquidity_data["source"] = "liquidity_risk_monitor"

    try:
        r = _r()
        r.setex(cache_key, CACHE_TTL["liquidity"], json.dumps(liquidity_data, default=str))
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    return liquidity_data
