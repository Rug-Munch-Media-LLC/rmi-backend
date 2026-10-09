"""Scam Detection Helpers for Market Intelligence - extracted from scams.py to reduce file size."""

from __future__ import annotations

import asyncio
from typing import Any

from app.routers.market_intel.providers import _ds
from app.routers.market_intel.utils import _safe_float


async def _fetch_pairs_for_boosts(boosts: list[dict], semaphore_limit: int = 8) -> list[tuple[dict, Any]]:
    """Fetch pair data for a list of boosts from DexScreener."""
    sem = asyncio.Semaphore(semaphore_limit)

    async def _pairs(addr: str) -> Any:
        if not addr:
            return None
        async with sem:
            return await _ds(f"/latest/dex/search?q={addr}", ttl=120)

    pairs_all = await asyncio.gather(*(_pairs(b.get("tokenAddress", "")) for b in boosts))
    return list(zip(boosts, pairs_all, strict=False))


def _extract_token_data(pairs_data: Any, fallback_symbol: str) -> dict[str, Any]:
    """Extract token data from DexScreener pairs response."""
    price = 0.0
    mcap = 0.0
    liquidity = 0.0
    vol_24h = 0.0
    change_24h = 0.0
    symbol = fallback_symbol
    name = ""

    if pairs_data and isinstance(pairs_data, dict) and pairs_data.get("pairs"):
        p = pairs_data["pairs"][0]
        bt = p.get("baseToken", {})
        symbol = bt.get("symbol", symbol).upper()
        name = bt.get("name", "")
        price = _safe_float(p.get("priceUsd"))
        mcap = _safe_float(p.get("fdv"))
        liquidity = _safe_float(p.get("liquidity", {}).get("usd") if isinstance(p.get("liquidity"), dict) else 0)
        vol_24h = _safe_float((p.get("volume") or {}).get("h24") if isinstance(p.get("volume"), dict) else 0)
        change_24h = _safe_float(
            (p.get("priceChange") or {}).get("h24") if isinstance(p.get("priceChange"), dict) else 0
        )

    return {
        "price": price,
        "mcap": mcap,
        "liquidity": liquidity,
        "vol_24h": vol_24h,
        "change_24h": change_24h,
        "symbol": symbol,
        "name": name,
    }


def _classify_scam(token_data: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Classify scam type, severity, and indicators for /scams endpoint."""
    change_24h = token_data["change_24h"]
    price = token_data["price"]
    mcap = token_data["mcap"]
    liquidity = token_data["liquidity"]
    vol_24h = token_data["vol_24h"]

    is_scam = (
        abs(change_24h) > 300
        or (mcap > 0 and liquidity > 0 and liquidity / mcap < 0.01)
        or (price > 0 and price < 0.00001 and vol_24h > 100000)
    )

    if not is_scam:
        return "", "", []

    if change_24h > 500:
        scam_type = "rug_pull"
        severity = "critical"
    elif price < 0.0001:
        scam_type = "honeypot"
        severity = "high"
    else:
        scam_type, severity = "copycat", "high" if change_24h > 300 else "medium"

    indicators = []
    if change_24h > 300:
        indicators.append(f"+{change_24h:.0f}% 24h pump")
    if mcap > 0 and liquidity > 0 and liquidity / mcap < 0.01:
        indicators.append(f"Low liquidity ratio ({liquidity / mcap * 100:.1f}%)")
    if mcap > 0 and mcap < 50000:
        indicators.append("Micro-cap (<$50K)")
    if not indicators:
        indicators.append("Suspicious trading pattern")

    return scam_type, severity, indicators[:4]


def _classify_alert(token_data: dict[str, Any]) -> tuple[str, str, str]:
    """Classify alert type, severity, and description for /alerts-feed endpoint."""
    change_24h = token_data["change_24h"]
    price = token_data["price"]
    mcap = token_data["mcap"]
    liquidity = token_data["liquidity"]
    vol_24h = token_data["vol_24h"]
    symbol = token_data["symbol"]

    if change_24h > 500:
        alert_type, severity = "pump_dump", "critical"
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
        alert_type, severity = "suspicious", "medium"
        desc = f"{symbol} showing anomalous trading. {change_24h:+.0f}% 24h, ${liquidity:,.0f} liq."

    return alert_type, severity, desc


def _build_scam_item(
    boost: dict[str, Any],
    chain: str,
    token_data: dict[str, Any],
    scam_type: str,
    severity: str,
    indicators: list[str],
    now: str,
) -> dict[str, Any]:
    """Build scam item dict for /scams endpoint."""
    addr = boost.get("tokenAddress", "")
    symbol = token_data["symbol"]
    name = token_data["name"]
    liquidity = token_data["liquidity"]
    vol_24h = token_data["vol_24h"]
    change_24h = token_data["change_24h"]

    return {
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
        "indicators": indicators,
    }


def _build_alert_item(
    boost: dict[str, Any],
    chain: str,
    token_data: dict[str, Any],
    alert_type: str,
    severity: str,
    desc: str,
    now: str,
) -> dict[str, Any]:
    """Build alert item dict for /alerts-feed endpoint."""
    addr = boost.get("tokenAddress", "")
    symbol = token_data["symbol"]
    price = token_data["price"]
    change_24h = token_data["change_24h"]
    liquidity = token_data["liquidity"]
    mcap = token_data["mcap"]
    vol_24h = token_data["vol_24h"]

    return {
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
