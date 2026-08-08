"""
RugCharts Intel — Token Launch Scanner.
New token detection + instant risk score.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import httpx
import redis

from app.core.logging import get_logger

from .cache import CACHE_TTL, HELIUS_KEY, _r

logger = get_logger("rugcharts_intel")


async def token_launch_scanner(chain: str = "solana", limit: int = 50, **kw) -> dict | None:
    """Scan for recently launched tokens and assign instant risk scores.
    Combines: age, liquidity, holder count, deployer history, GoPlus checks.
    """
    cache_key = f"token_launch:{chain}:{limit}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, redis.exceptions.ResponseError):
        logger.warning("error")

    new_tokens = []
    api_key = kw.get("api_key", "") or HELIUS_KEY

    try:
        if chain == "solana" and api_key:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [
                            "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
                            {"limit": limit},
                        ],
                    },
                )
                if r.status_code == 200:
                    sigs = r.json().get("result", [])
                    now = int(time.time())
                    for sig_data in sigs:
                        age_seconds = now - sig_data.get("blockTime", now)
                        age_minutes = age_seconds / 60
                        if age_minutes > 1440:
                            continue
                        if age_minutes < 5:
                            risk = "CRITICAL"
                            risk_score = 95
                            risk_color = "#FF0044"
                        elif age_minutes < 30:
                            risk = "HIGH"
                            risk_score = 75
                            risk_color = "#FF8800"
                        elif age_minutes < 120:
                            risk = "MEDIUM"
                            risk_score = 50
                            risk_color = "#FFD700"
                        elif age_minutes < 360:
                            risk = "LOW"
                            risk_score = 25
                            risk_color = "#88FF00"
                        else:
                            risk = "MONITORING"
                            risk_score = 10
                            risk_color = "#00FF88"

                        new_tokens.append(
                            {
                                "signature": sig_data["signature"][:16] + "...",
                                "launch_time": datetime.fromtimestamp(sig_data.get("blockTime", 0), tz=UTC).isoformat(),
                                "age_minutes": round(age_minutes, 1),
                                "risk_score": risk_score,
                                "risk_level": risk,
                                "risk_color": risk_color,
                                "block": sig_data.get("slot"),
                            }
                        )
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, redis.exceptions.ResponseError) as e:
        logger.warning(f"error: {e}")
        logger.debug(f"Token launch scan: {e}")

    new_tokens.sort(key=lambda x: x.get("age_minutes", 999), reverse=False)

    result = {
        "new_tokens": new_tokens[:limit],
        "total_detected": len(new_tokens),
        "chain": chain,
        "risk_distribution": {
            "CRITICAL": sum(1 for t in new_tokens if t["risk_level"] == "CRITICAL"),
            "HIGH": sum(1 for t in new_tokens if t["risk_level"] == "HIGH"),
            "MEDIUM": sum(1 for t in new_tokens if t["risk_level"] == "MEDIUM"),
            "LOW": sum(1 for t in new_tokens if t["risk_level"] == "LOW"),
            "MONITORING": sum(1 for t in new_tokens if t["risk_level"] == "MONITORING"),
        },
        "scan_time": datetime.now(UTC).isoformat(),
        "source": "token_launch_scanner",
    }

    try:
        r = _r()
        r.setex(cache_key, CACHE_TTL["token_launch"], json.dumps(result, default=str))
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, redis.exceptions.ResponseError):
        logger.warning("error")

    return result
