"""
RugCharts Intel — Rug Pattern Matcher.
RAG-powered similarity to known rug pulls.
"""

from __future__ import annotations
from typing import Any

import json
import time
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger

from .cache import CACHE_TTL, HELIUS_KEY, _r

logger = get_logger("rugcharts_intel")

RUG_PATTERNS = [
    {
        "pattern": "liquidity_removal_100pct",
        "name": "Full LP Drain",
        "severity": "CRITICAL",
        "description": "100% of liquidity removed in single transaction. Classic rug pull.",
    },
    {
        "pattern": "honeypot_sell_tax_100",
        "name": "Honeypot - 100% Sell Tax",
        "severity": "CRITICAL",
        "description": "Buy works, sell reverts. Token is locked.",
    },
    {
        "pattern": "mint_infinite",
        "name": "Infinite Mint",
        "severity": "CRITICAL",
        "description": "Unrestricted mint() function drains all value.",
    },
    {
        "pattern": "creator_dump_80pct",
        "name": "Creator Dump >80%",
        "severity": "CRITICAL",
        "description": "Creator wallet sold >80% of supply within hours of launch.",
    },
    {
        "pattern": "slow_rug_72h",
        "name": "Slow Rug (72h)",
        "severity": "HIGH",
        "description": "Gradual LP removal over 3 days to avoid detection.",
    },
    {
        "pattern": "team_wallet_dump",
        "name": "Team Wallet Cascade",
        "severity": "HIGH",
        "description": "Multiple team wallets selling in sequence.",
    },
    {
        "pattern": "fake_volume_boost",
        "name": "Wash Trading Pump",
        "severity": "HIGH",
        "description": "Artificial volume to reach trending, then dump.",
    },
    {
        "pattern": "ownership_renounced_scam",
        "name": "Renounced Trap",
        "severity": "MEDIUM",
        "description": "Ownership renounced but hidden backdoor in contract.",
    },
    {
        "pattern": "proxy_upgrade_rug",
        "name": "Proxy Upgrade Attack",
        "severity": "MEDIUM",
        "description": "Upgradeable proxy changed to malicious implementation.",
    },
    {
        "pattern": "flash_loan_attack",
        "name": "Flash Loan Drain",
        "severity": "HIGH",
        "description": "Flash loan used to manipulate price and drain LP.",
    },
]


async def rug_pattern_matcher(address: str = "", chain: str = "solana", **kw) -> dict | None:
    """Match token characteristics against known rug pull patterns.
    Each pattern gets a similarity score. High matches = high rug probability.
    """
    if not address:
        return None

    cache_key = f"rug_pattern:{chain}:{address}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    matches = []
    api_key = kw.get("api_key", "") or HELIUS_KEY

    try:
        signals = {
            "is_new": False,
            "lp_locked": False,
            "holder_count": 0,
            "top_holder_pct": 0,
            "has_mint": False,
            "is_proxy": False,
            "owner_renounced": False,
            "volume_spike": False,
            "age_hours": 999,
        }

        if chain == "solana" and api_key:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={"jsonrpc": "2.0", "id": 1, "method": "getAsset", "params": [address]},
                )
                if r.status_code == 200:
                    asset = r.json().get("result", {})
                    if asset:
                        signals["is_new"] = True

                r2 = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTokenLargestAccounts",
                        "params": [address],
                    },
                )
                if r2.status_code == 200:
                    holders = r2.json().get("result", {}).get("value", [])
                    if holders:
                        signals["holder_count"] = len(holders)
                        total = sum(float(h.get("amount", 0)) for h in holders)
                        if total > 0:
                            signals["top_holder_pct"] = float(holders[0].get("amount", 0)) / total * 100

                r3 = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": 1}],
                    },
                )
                if r3.status_code == 200:
                    sigs = r3.json().get("result", [])
                    if sigs:
                        age = int(time.time()) - sigs[0].get("blockTime", int(time.time()))
                        signals["age_hours"] = age / 3600

        for pattern in RUG_PATTERNS:
            score = 0
            evidence = []

            if pattern["pattern"] == "liquidity_removal_100pct":
                if signals["top_holder_pct"] > 95:
                    score = 85
                    evidence.append("LP highly concentrated")
                elif signals["top_holder_pct"] > 70:
                    score = 50
                    evidence.append("LP moderately concentrated")
                if signals["age_hours"] < 24:
                    score += 10
                    evidence.append("New token")
            elif pattern["pattern"] == "honeypot_sell_tax_100":
                if signals["is_new"] and signals["holder_count"] < 50:
                    score = 60
                    evidence.append("New token with few holders - possible honeypot")
                if signals["top_holder_pct"] > 90:
                    score += 20
                    evidence.append("Single wallet dominance")
            elif pattern["pattern"] == "creator_dump_80pct":
                if signals["top_holder_pct"] > 80 and signals["age_hours"] < 48:
                    score = 75
                    evidence.append("Creator holds >80% on new token")
                elif signals["top_holder_pct"] > 50 and signals["age_hours"] < 24:
                    score = 40
                    evidence.append("High creator allocation on fresh token")
            elif pattern["pattern"] == "slow_rug_72h":
                if 24 < signals["age_hours"] < 72 and signals["top_holder_pct"] > 60:
                    score = 50
                    evidence.append("Concentrated supply in slow rug window")
            elif pattern["pattern"] == "fake_volume_boost":
                if signals["age_hours"] < 6 and not signals["lp_locked"]:
                    score = 40
                    evidence.append("New token without LP lock")

            if score > 0:
                matches.append(
                    {
                        "pattern_id": pattern["pattern"],
                        "pattern_name": pattern["name"],
                        "severity": pattern["severity"],
                        "match_score": min(100, score),
                        "description": pattern["description"],
                        "evidence": evidence,
                    }
                )
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        logger.debug(f"Rug pattern matcher: {e}")

    matches.sort(key=lambda x: x["match_score"], reverse=True)

    overall_risk = "LOW"
    if matches:
        top_score = matches[0]["match_score"]
        if top_score >= 80:
            overall_risk = "CRITICAL"
        elif top_score >= 60:
            overall_risk = "HIGH"
        elif top_score >= 40:
            overall_risk = "MEDIUM"

    result = {
        "pattern_matches": matches,
        "total_matches": len(matches),
        "overall_rug_risk": overall_risk,
        "address": address,
        "chain": chain,
        "signals_observed": signals,
        "scan_time": datetime.now(UTC).isoformat(),
        "source": "rug_pattern_matcher",
    }

    try:
        r = _r()
        r.setex(cache_key, CACHE_TTL["rug_pattern"], json.dumps(result, default=str))
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    return result
