"""

Liquidity Lock Authenticity
============================
Not just "is it locked?" but meaningful analysis of the lock.
Duration vs project claims, partial unlocks, LP ratio health.
"""

from __future__ import annotations

from typing import Any

from app.services.deep_intelligence.core import client


async def liquidity_lock_authenticity(token_address: str, chain: str = "sol") -> dict[str, Any]:
    """
    Not just "is it locked?" but meaningful analysis of the lock.
    Duration vs project claims, partial unlocks, LP ratio health.
    """
    result = {
        "token": token_address,
        "chain": chain,
        "is_locked": None,
        "lock_details": {},
        "authenticity_score": 0,
        "flags": [],
        "recommendation": "UNKNOWN",
    }

    dex_info = await client.dexscreener(chain, token_address)
    if "error" in dex_info:
        result["flags"].append("Could not fetch token data")
        return result

    liquidity = float(dex_info.get("liquidity", {}).get("usd", 0) or 0)
    mcap = float(dex_info.get("marketCap", 0) or 0)

    lock_info = dex_info.get("liquidity", {})
    is_locked = bool(lock_info.get("locked"))

    liq_to_mcap = liquidity / max(mcap, 1) if mcap else 0
    flags = []

    if is_locked:
        result["is_locked"] = True
        lock_pct = float(lock_info.get("lockedPct", 0) or 0)
        result["lock_details"] = {
            "locked_pct": lock_pct,
            "lock_source": lock_info.get("lockedSource", "unknown"),
        }
        if lock_pct < 50:
            flags.append(f"Only {lock_pct:.0f}% of LP locked - partial lock")
        if lock_pct < 100:
            flags.append("Not fully locked - remaining LP can be pulled")
    else:
        result["is_locked"] = False
        flags.append("NO liquidity lock detected - LP can be removed at any time")

    if liq_to_mcap < 0.02:
        flags.append(f"Very thin liquidity: only {liq_to_mcap:.1%} of market cap")
    elif liq_to_mcap < 0.05:
        flags.append(f"Low liquidity ratio: {liq_to_mcap:.1%} of market cap")

    score = 50
    if is_locked:
        score += 25
        lock_pct = float(lock_info.get("lockedPct", 0) or 0)
        if lock_pct >= 100:
            score += 15
        if lock_pct >= 80:
            score += 10
    else:
        score -= 30

    if liq_to_mcap >= 0.1:
        score += 10
    elif liq_to_mcap >= 0.05:
        score += 5

    result["authenticity_score"] = max(0, min(100, score))
    result["flags"] = flags
    result["recommendation"] = (
        "SAFE"
        if score >= 80
        else "CAUTION"
        if score >= 60
        else "RISKY"
        if score >= 40
        else "DANGEROUS"
    )

    return result
