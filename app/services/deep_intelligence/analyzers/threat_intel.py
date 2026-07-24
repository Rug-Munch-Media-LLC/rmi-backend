"""

Threat Intel Check
===================
Aggregate on-chain threat intelligence from GoPlus, Webacy, and Chainabuse.
"""

from __future__ import annotations

from typing import Any

from app.services.deep_intelligence.core import client


async def threat_intel_check(address: str, chain: str = "sol") -> dict[str, Any]:
    """
    Aggregate on-chain threat intelligence from GoPlus, Webacy, and Chainabuse.
    """
    result = {
        "address": address,
        "chain": chain,
        "goplus": None,
        "webacy": None,
        "chainabuse": None,
        "risk_flags": [],
        "risk_score": 0,
        "verdict": "UNKNOWN",
    }

    flags = []
    score = 0

    # 1. GoPlus Token Security
    goplus_data = await client.goplus_get(chain, address)
    if "error" not in goplus_data:
        result["goplus"] = goplus_data
        token_data = goplus_data.get("result", {}).get(address.lower(), {})
        if token_data:
            if token_data.get("is_honeypot") == "1":
                flags.append("GoPlus: Honeypot detected")
                score += 40
            if token_data.get("is_mintable") == "1":
                flags.append("GoPlus: Token is mintable")
                score += 15
            if token_data.get("is_proxy") == "1":
                flags.append("GoPlus: Proxy contract detected")
                score += 10
            if token_data.get("owner_change_balance") == "1":
                flags.append("GoPlus: Owner can change balance")
                score += 20

    # 2. Webacy Threat Intel
    webacy_data = await client.webacy_get(chain, address)
    if "error" not in webacy_data:
        result["webacy"] = webacy_data
        if isinstance(webacy_data, dict):
            risk_score = webacy_data.get("risk_score", 0)
            if risk_score > 50:
                flags.append(f"Webacy: High risk score ({risk_score})")
                score += 20

    # 3. Chainabuse Reports
    chainabuse_data = await client.chainabuse_get(address)
    if "error" not in chainabuse_data:
        result["chainabuse"] = chainabuse_data
        reports = chainabuse_data.get("reports", [])
        if isinstance(reports, list) and len(reports) > 0:
            flags.append(f"Chainabuse: {len(reports)} abuse report(s) found")
            score += 30

    result["risk_flags"] = flags
    result["risk_score"] = min(score, 100)
    result["verdict"] = (
        "CRITICAL"
        if score >= 60
        else "HIGH_RISK"
        if score >= 40
        else "SUSPICIOUS"
        if score >= 20
        else "CLEAN"
    )

    return result
