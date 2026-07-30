"""
Cross-Chain Trace — Bridge Detection
=====================================
"""

from __future__ import annotations

from .constants import _BRIDGE_CONTRACTS


def _is_bridge_contract(address: str, chain: str) -> bool:
    """Check if an address is a known bridge contract on the given chain."""
    addr = address.lower()
    chain_lower = chain.lower()
    bridges = _BRIDGE_CONTRACTS.get(chain_lower, set())
    return addr in bridges


def _infer_bridge_destination(
    from_addr: str,
    to_addr: str,
    chain: str,
) -> str | None:
    """Try to infer bridge destination chain from transfer endpoints."""
    if _is_bridge_contract(to_addr, chain):
        if chain == "ethereum":
            return "arbitrum"
        if chain in ("bsc", "base", "arbitrum", "polygon", "optimism"):
            return "ethereum"
        return "ethereum"

    if _is_bridge_contract(from_addr, chain):
        return "ethereum"

    return None
