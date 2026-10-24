"""DexScreener liquidity/age/volume signal for the Rug Probability tool.

Extracted verbatim from ``rug_probability.py``: the router keeps the orchestration
and this module owns the one network call plus its ladder. Returns ordered
``(points, signal)`` pairs — the caller sums the points and appends the dicts, so
ordering and totals are identical to the inline version.
"""

from __future__ import annotations

import json
import time
from typing import Any

import aiohttp

from app.core.logging import get_logger

logger = get_logger("x402.rug_probability")


def _liquidity_hits(liq: float) -> list[tuple[int, dict[str, Any]]]:
    if liq < 1000:
        return [
            (
                25,
                {
                    "signal": "critical_low_liquidity",
                    "band": "extreme",
                    "detail": f"Liquidity ${liq:,.0f}",
                },
            )
        ]
    if liq < 10000:
        return [(15, {"signal": "low_liquidity", "band": "high", "detail": f"Liquidity ${liq:,.0f}"})]
    if liq < 50000:
        return [(5, {"signal": "moderate_liquidity", "band": "low", "detail": f"Liquidity ${liq:,.0f}"})]
    return []


def _age_hits(age_h: float) -> list[tuple[int, dict[str, Any]]]:
    if 0 < age_h < 1:
        return [(20, {"signal": "brand_new", "band": "high", "detail": f"Only {age_h:.1f}h old"})]
    if 0 < age_h < 6:
        return [(12, {"signal": "very_new", "band": "moderate", "detail": f"Only {age_h:.1f}h old"})]
    if 0 < age_h < 24:
        return [(5, {"signal": "new_token", "band": "low", "detail": f"{age_h:.0f}h old"})]
    return []


def _flow_hits(liq: float, vol: float, pc: float) -> list[tuple[int, dict[str, Any]]]:
    hits: list[tuple[int, dict[str, Any]]] = []
    if liq > 0 and vol > liq * 3:
        hits.append(
            (
                10,
                {
                    "signal": "pump_dump_pattern",
                    "band": "moderate",
                    "detail": f"Volume {vol / liq:.0f}x liquidity",
                },
            )
        )
    if pc < -50:
        hits.append((15, {"signal": "price_crashing", "band": "high", "detail": f"Down {pc:.0f}% in 24h"}))
    elif pc < -20:
        hits.append(
            (
                8,
                {
                    "signal": "price_declining",
                    "band": "moderate",
                    "detail": f"Down {pc:.0f}% in 24h",
                },
            )
        )
    return hits


def _pair_hits(pair: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    liq = pair.get("liquidity", {}).get("usd", 0) or 0
    vol = pair.get("volume", {}).get("h24", 0) or 0
    age_ms = pair.get("pairCreatedAt", 0) or 0
    age_h = (time.time() * 1000 - age_ms) / 3600000 if age_ms else 0
    pc = pair.get("priceChange", {}).get("h24", 0) or 0
    return _liquidity_hits(liq) + _age_hits(age_h) + _flow_hits(liq, vol, pc)


async def dexscreener_hits(addr: str) -> list[tuple[int, dict[str, Any]]]:
    """Fetch the first DexScreener pair and return its ordered risk signals."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{addr}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        return _pair_hits(pairs[0])
    except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
        logger.debug(f"DexScreener failed: {e}")
    return []
