#!/usr/bin/env python3
"""#19 - Smart Contract Function Analyzer. Extends SENTINEL to decompile and analyze

bytecode. Returns human-readable "what this contract actually does."

Dependency injection: Settings and HTTP client are injected via ``Depends()``
so they can be mocked in tests.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_http_client, get_settings
from app.core.internal_key import INTERNAL_API_KEY

if TYPE_CHECKING:
    from app.config import Settings

router = APIRouter(prefix="/api/v1/contract-analyzer", tags=["contract-analyzer"])

# Known suspicious function signatures (4-byte selectors)
SUSPICIOUS_SELECTORS = {
    "0x18160ddd": "totalSupply()",
    "0x06fdde03": "name()",
    "0x95d89b41": "symbol()",
    "0x313ce567": "decimals()",
    "0x70a08231": "balanceOf(address)",
    "0xa9059cbb": "transfer(address,uint256)",
    "0x23b872dd": "transferFrom(address,address,uint256)",
    "0x095ea7b3": "approve(address,uint256)",
    "0x42966c68": "burn(uint256)",
    "0x79cc6790": "burnFrom(address,uint256)",
    "0xf2fde38b": "transferOwnership(address)",
    "0x8da5cb5b": "owner()",
    "0x8456cb59": "pause()",
    "0x3f4ba83a": "unpause()",
    "0x40c10f19": "mint(address,uint256)",
    "0x9dc29fac": "burn(uint256)",
    "0x79ba5097": "renounceOwnership()",
    "0x715018a6": "renounceOwnership()",
    "0x83197ef0": "includeInReward(address)",
    "0xea8a1af0": "excludeFromReward(address)",
    "0x7f41468d": "setSwapAndLiquifyEnabled(bool)",
    "0x55505aac": "setMaxTxPercent(uint256)",
    "0x3141de2d": "setMaxWalletPercent(uint256)",
}

# Patterns that indicate malicious intent
MALICIOUS_PATTERNS = [
    ("unlimited mint", [r"mint\(", r"_mint\(", r"mint_without_limit"]),
    ("hidden owner functions", [r"onlyOwner", r"require\(.*owner"]),
    ("transfer fee manipulation", [r"setTax", r"setFee", r"updateFee"]),
    ("blacklist capability", [r"blacklist", r"isBlacklisted", r"_blacklist"]),
    ("pause/trading halt", [r"pause\(", r"unpause\(", r"setTrading"]),
    ("reentrancy risk", [r"\.call\{", r"\.transfer\("]),
    ("selfdestruct", [r"selfdestruct\(", r"suicide\("]),
    ("proxy upgrade risk", [r"upgradeTo\(", r"upgradeToAndCall\(", r"_upgradeTo"]),
]


async def _get_contract_bytecode(address: str, chain: str, client: httpx.AsyncClient) -> dict[str, Any]:
    """Fetch contract bytecode and source from Etherscan/Blockscout."""
    explorers = {
        "ethereum": "https://api.etherscan.io/api",
        "bsc": "https://api.bscscan.com/api",
        "polygon": "https://api.polygonscan.com/api",
        "arbitrum": "https://api.arbiscan.io/api",
        "base": "https://api.basescan.org/api",
        "optimism": "https://api-optimistic.etherscan.io/api",
    }

    result = {"bytecode": "", "source": "", "abi": [], "verified": False}

    api_base = explorers.get(chain)
    if not api_base:
        return result

    try:
        resp = await client.get(
            f"{api_base}",
            params={
                "module": "proxy",
                "action": "eth_getCode",
                "address": address,
                "apikey": "YourApiKeyToken",
            },
        )
        if resp.status_code == 200:
            result["bytecode"] = resp.json().get("result", "")

        resp2 = await client.get(
            f"{api_base}",
            params={
                "module": "contract",
                "action": "getsourcecode",
                "address": address,
                "apikey": "YourApiKeyToken",
            },
        )
        if resp2.status_code == 200:
            data = resp2.json().get("result", [{}])[0]
            result["source"] = data.get("SourceCode", "")
            result["abi"] = data.get("ABI", "[]")
            result["verified"] = True
    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        logger.warning("Contract analyzer API error: %s", e)

    return result


def _analyze_bytecode(bytecode: str, source: str) -> dict:
    """Analyze contract bytecode/source for suspicious patterns."""
    findings: list[dict] = []
    risk_score = 0

    for selector, name in SUSPICIOUS_SELECTORS.items():
        if selector[2:] in bytecode.lower():
            risk_map = {
                "mint": 15,
                "burn": 5,
                "pause": 10,
                "transferOwnership": 10,
                "renounceOwnership": 5,
                "includeInReward": 10,
                "setSwapAndLiquifyEnabled": 10,
                "setMaxTxPercent": 10,
                "setMaxWalletPercent": 10,
            }
            for key, score in risk_map.items():
                if key in name:
                    risk_score += score
                    findings.append(
                        {
                            "function": name,
                            "selector": selector,
                            "risk": "HIGH" if score >= 10 else "MEDIUM",
                            "explanation": f"Contract has {name} function - {'can create unlimited tokens' if 'mint' in name.lower() else 'may be used to manipulate trading'}",
                        }
                    )
                    break

    if source:
        for pattern_name, regexes in MALICIOUS_PATTERNS:
            for regex in regexes:
                if re.search(regex, source, re.IGNORECASE):
                    risk_score += 10
                    findings.append(
                        {
                            "pattern": pattern_name,
                            "matched": regex,
                            "risk": "HIGH",
                            "explanation": f"Source code contains {pattern_name} pattern",
                        }
                    )
                    break

    risk_level = (
        "CRITICAL" if risk_score >= 50 else "HIGH" if risk_score >= 25 else "MEDIUM" if risk_score >= 10 else "LOW"
    )
    return {"risk_score": min(100, risk_score), "risk_level": risk_level, "findings": findings}


@router.get("/analyze/{address}")
async def analyze_contract(
    address: str,
    chain: str = Query("ethereum"),
    client: httpx.AsyncClient = Depends(get_http_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Analyze a smart contract's functions and risk profile."""
    backend = settings.backend_url
    info = await _get_contract_bytecode(address, chain, client)
    analysis = _analyze_bytecode(info.get("bytecode", ""), info.get("source", ""))

    # Also run SENTINEL scan
    try:
        resp = await client.post(
            f"{backend}/api/v1/token/scan",
            json={"token_address": address, "chain": chain},
            headers={"X-RMI-Key": INTERNAL_API_KEY},
        )
        if resp.status_code == 200:
            sentinel = resp.json()
            analysis["sentinel_score"] = sentinel.get("safety_score", 50)
            analysis["sentinel_flags"] = sentinel.get("risk_flags", [])
    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        logger.warning("Contract analyzer API error: %s", e)

    return {
        "contract": address,
        "chain": chain,
        "verified": info.get("verified", False),
        "bytecode_length": len(info.get("bytecode", "")),
        "analysis": analysis,
        "summary": (
            f"Risk: {analysis['risk_level']} ({analysis['risk_score']}/100). "
            f"{len(analysis['findings'])} suspicious patterns found. "
            f"Verified: {'Yes' if info.get('verified') else 'No'}."
        ),
    }


@router.get("/detect-mint/{address}")
async def detect_mint_function(
    address: str,
    chain: str = Query("ethereum"),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> dict:
    """Quick check: does this contract have a mint function?"""
    info = await _get_contract_bytecode(address, chain, client)
    bytecode = info.get("bytecode", "")

    mint_selectors = ["40c10f19", "079cc6790", "a0712d68"]
    found = []
    for sel in mint_selectors:
        if sel in bytecode.lower():
            name = SUSPICIOUS_SELECTORS.get(f"0x{sel}", "unknown mint function")
            found.append({"selector": f"0x{sel}", "function": name})
    return {
        "contract": address,
        "has_mint_function": len(found) > 0,
        "mint_functions": found,
        "risk": "HIGH" if len(found) > 0 else "LOW",
        "note": "Mint functions allow creating new tokens - typical of scams" if found else "No mint function detected",
    }
