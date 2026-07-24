"""

Ownership Theater Detector
===========================
Detect fake renunciation: hidden admin roles, upgradeable proxies,
secondary controlled addresses, remaining privileges.
"""

from __future__ import annotations

from typing import Any

from app.services.deep_intelligence.core import cfg, client

async def ownership_theater_detector(token_address: str, chain: str = "sol") -> dict[str, Any]:
    """
    Detect fake renunciation: hidden admin roles, upgradeable proxies,
    secondary controlled addresses, remaining privileges.
    """
    result = {
        "token": token_address,
        "chain": chain,
        "is_renounced": None,
        "renunciation_authentic": None,
        "hidden_privileges": [],
        "risk_flags": [],
        "theater_score": 0,
        "verdict": "UNKNOWN",
    }

    dex_info = await client.dexscreener(chain, token_address)

    privileges = []
    flags = []
    theater_score = 0

    is_renounced = False
    if "error" not in dex_info:
        tags = dex_info.get("tags", []) or []
        if "renounced" in [str(t).lower() for t in tags]:
            is_renounced = True

    if chain in cfg.CHAIN_SCAN_MAP:
        contract_info = await client.scan_get(
            chain,
            {
                "module": "contract",
                "action": "getsourcecode",
                "address": token_address,
            },
        )
        if contract_info.get("status") == "1":
            source_data = (
                contract_info.get("result", [{}])[0] if contract_info.get("result") else {}
            )
            is_proxy = bool(
                source_data.get("Proxy", "0") == "1" or source_data.get("Implementation", "")
            )

            if is_proxy:
                flags.append("UPGRADEABLE PROXY - owner can change contract logic at any time")
                theater_score += 40
                privileges.append("Proxy contract with upgradeable implementation")

            abi_str = source_data.get("ABI", "")
            if "mint" in abi_str.lower():
                flags.append("Mint function found - tokens can be created out of thin air")
                theater_score += 30
                privileges.append("Mint function exists")
            if "pause" in abi_str.lower():
                flags.append("Pause function - contract can be frozen")
                theater_score += 15
                privileges.append("Pause function exists")
            if "blacklist" in abi_str.lower():
                flags.append("Blacklist function - selective wallet blocking")
                theater_score += 25
                privileges.append("Blacklist function exists")

    result["is_renounced"] = is_renounced
    result["hidden_privileges"] = privileges
    result["risk_flags"] = flags
    result["theater_score"] = min(theater_score, 100)

    if is_renounced and theater_score > 0:
        result["renunciation_authentic"] = False
        result["verdict"] = "OWNERSHIP_THEATER"
    elif is_renounced:
        result["renunciation_authentic"] = True
        result["verdict"] = "GENUINE_RENUNCIATION"
    elif theater_score > 30:
        result["verdict"] = "SUSPICIOUS_PRIVILEGES"
    else:
        result["verdict"] = "NOT_RENOUNCED"

    return result
