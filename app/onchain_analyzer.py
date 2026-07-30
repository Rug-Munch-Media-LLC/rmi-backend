"""Stub onchain_analyzer - onchain smart contract analysis"""

from __future__ import annotations

from typing import Any


def get_w3(chain: str = "ethereum") -> None:
    """Get web3 instance for a chain (stub - returns None for testing)."""
    return None


def analyze_contract(contract_address: str, chain: str = "base") -> dict[str, Any]:
    """Analyze a single contract."""
    return {
        "address": contract_address,
        "chain": chain,
        "analyzed": False,
        "issues": [],
        "recommendations": [],
    }


def batch_analyze_contracts(addresses: list[str], chain: str = "base") -> dict[str, Any]:
    """Batch analyze contracts."""
    results = []
    for addr in addresses:
        results.append(analyze_contract(addr, chain))
    return {"contracts": results, "total": len(results)}
