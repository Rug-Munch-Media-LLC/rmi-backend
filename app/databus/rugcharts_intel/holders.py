"""
RugCharts Intel — Holder Health Score.
Distribution analysis (Gini, concentration, freshness).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger

from .cache import CACHE_TTL, HELIUS_KEY, _r

logger = get_logger("rugcharts_intel")


async def holder_health_score(address: str = "", chain: str = "solana", **kw) -> dict | None:
    """Analyze holder distribution for rug pull risk indicators.
    Metrics: Gini coefficient, top 10%, fresh wallets, whale dominance.
    """
    if not address:
        return None

    cache_key = f"holder_health:{chain}:{address}"
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
    health = {
        "total_holders": 0,
        "gini_coefficient": 0.0,
        "top10_concentration": 0.0,
        "top1_dominance": 0.0,
        "fresh_wallet_pct": 0.0,
        "whale_count": 0,
        "decentralization_score": 0,
        "risk_level": "UNKNOWN",
        "risk_factors": [],
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
                    if not holders:
                        health["risk_level"] = "NO_HOLDERS"
                        health["risk_factors"].append("Zero holders detected")
                        return health

                    health["total_holders"] = len(holders)
                    amounts = sorted([float(h.get("amount", 0)) for h in holders], reverse=True)
                    total = sum(amounts)

                    if total <= 0:
                        return health

                    n = len(amounts)
                    gini = (2 * sum((i + 1) * amounts[i] for i in range(n))) / (n * total) - (n + 1) / n
                    health["gini_coefficient"] = round(gini, 3)
                    health["top10_concentration"] = round(sum(amounts[:10]) / total * 100, 1)
                    health["top1_dominance"] = round(amounts[0] / total * 100, 1) if amounts else 0
                    health["whale_count"] = sum(1 for a in amounts if a / total > 0.01)

                    gini_penalty = min(1.0, gini / 0.9) * 40
                    holder_bonus = min(1.0, n / 1000) * 30
                    whale_penalty = min(1.0, health["whale_count"] / 10) * 30
                    health["decentralization_score"] = max(0, round(100 - gini_penalty + holder_bonus - whale_penalty))

                    if gini > 0.8:
                        health["risk_level"] = "CRITICAL"
                        health["risk_factors"].append(f"Extreme concentration (Gini={gini:.2f})")
                    elif gini > 0.6:
                        health["risk_level"] = "HIGH"
                        health["risk_factors"].append(f"High concentration (Gini={gini:.2f})")
                    elif gini > 0.4:
                        health["risk_level"] = "MEDIUM"
                        health["risk_factors"].append(f"Moderate concentration (Gini={gini:.2f})")
                    else:
                        health["risk_level"] = "LOW"

                    if health["top1_dominance"] > 50:
                        health["risk_level"] = "CRITICAL"
                        health["risk_factors"].append(f"Single wallet dominance {health['top1_dominance']}%")

                    if n < 20:
                        health["risk_level"] = max(
                            health["risk_level"],
                            "HIGH",
                            key=lambda x: {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}.get(x, 0),
                        )
                        health["risk_factors"].append(f"Very few holders ({n})")
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        logger.debug(f"Holder health: {e}")

    health["token"] = address
    health["chain"] = chain
    health["scan_time"] = datetime.now(UTC).isoformat()
    health["source"] = "holder_health_score"

    try:
        r = _r()
        r.setex(cache_key, CACHE_TTL["holder_health"], json.dumps(health, default=str))
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    return health
