"""
Layer 5: Historical Scanner Data — time-series risk/liquidity/holder data.

Reads scanner snapshots from Redis and DexScreener, computes trends over
a configurable lookback window.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiohttp

from app.core.errors import AppError
from app.core.logging import get_logger
from app.routers.x402_advanced_tools.cache import get_cached, set_cached
from app.routers.x402_advanced_tools.confidence import compute_confidence

if TYPE_CHECKING:
    from app.routers.x402_advanced_tools.models import HistoryRequest

logger = get_logger("x402.history")

async def token_history(req: HistoryRequest) -> Any:
    """Historical risk/liquidity/holder data for any token.

    Returns time-series data from the RMI scanner (runs every 10 min).
    Shows how risk profile, liquidity, volume, and holder metrics change over time.
    """
    try:
        addr = req.address.strip()
        chain = req.chain or "base"
        hours = req.hours

        cached = get_cached("history", {"address": addr, "chain": chain, "hours": hours})
        if cached:
            return cached

        import redis as _redis

        r = _redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=2,
        )

        data_points = []
        cutoff = time.time() - (hours * 3600)

        for key in r.scan_iter(f"x402:scan:{addr}:*"):
            try:
                k = key.decode() if isinstance(key, bytes) else key
                ts = float(k.split(":")[-1])
                if ts < cutoff:
                    continue
                raw = r.get(k)
                if raw:
                    dp = json.loads(raw)
                    dp["timestamp"] = ts
                    data_points.append(dp)
            except (ValueError, json.JSONDecodeError):
                continue

        for key in r.scan_iter(f"token:scan:{addr}:*"):
            try:
                k = key.decode() if isinstance(key, bytes) else key
                ts = float(k.split(":")[-1])
                if ts < cutoff:
                    continue
                raw = r.get(k)
                if raw:
                    dp = json.loads(raw)
                    dp["timestamp"] = ts
                    data_points.append(dp)
            except (ValueError, json.JSONDecodeError):
                continue

        data_points.sort(key=lambda d: d.get("timestamp", 0))

        # Add current snapshot from DexScreener
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.dexscreener.com/latest/dex/tokens/{addr}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        dex_data = await resp.json()
                        pairs = dex_data.get("pairs", [])
                        if pairs:
                            p = pairs[0]
                            current = {
                                "timestamp": time.time(),
                                "price_usd": p.get("priceUsd"),
                                "liquidity_usd": p.get("liquidity", {}).get("usd"),
                                "volume_24h": p.get("volume", {}).get("h24"),
                                "price_change_24h": p.get("priceChange", {}).get("h24"),
                                "is_current": True,
                            }
                            data_points.append(current)
        except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
            logger.debug(f"DexScreener snapshot failed: {e}")

        # Compute trends
        trends = {}
        if len(data_points) >= 2:
            first = data_points[0]
            last = data_points[-1]
            for metric in ["price_usd", "liquidity_usd", "volume_24h"]:
                fv = first.get(metric)
                lv = last.get(metric)
                if fv and lv and fv > 0:
                    change_pct = ((lv - fv) / fv) * 100
                    trends[metric] = {
                        "start": fv,
                        "end": lv,
                        "change_pct": round(change_pct, 1),
                        "direction": "up" if change_pct > 0 else "down" if change_pct < 0 else "flat",
                    }

        result = {
            "tool": "Historical Scanner Data",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "address": addr,
            "chain": chain,
            "lookback_hours": hours,
            "data_points": len(data_points),
            "history": data_points[-50:],
            "trends": trends,
            "scanner_interval": "10 minutes",
            "_confidence": compute_confidence({
                "sources_used": ["rmi_scanner"]
                + (["dexscreener"] if any(d.get("is_current") for d in data_points) else [])
            }),
            "guarantee": "Historical data or full refund",
        }

        set_cached("history", {"address": addr, "chain": chain, "hours": hours}, result, ttl=120)
        return result

    except Exception as e:
        logger.error(f"Token history failed: {e}")
        raise AppError(message=str(e)) from e

__all__ = ["token_history"]
