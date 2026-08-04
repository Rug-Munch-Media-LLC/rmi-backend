"""
Cross-Chain Trace — Utilities
==============================
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .constants import _EVM_CHAINS

if TYPE_CHECKING:
    from .models import CrossChainTraceReport


def _assign_role(
    address: str,
    is_seed: bool,
    label_category: str | None,
) -> str:
    """Assign a semantic role to an address based on its label and position."""
    if is_seed:
        return "origin"
    if label_category in ("exchange",):
        return "exchange"
    if label_category in ("mixer",):
        return "mixer"
    if label_category in ("scam", "exploiter", "malicious", "ponzi", "phishing_scam"):
        return "scam"
    if label_category in ("bridge",):
        return "bridge"
    if label_category in ("defi", "dex", "lending"):
        return "defi"
    if label_category in ("sanctioned", "darknet_market", "money_laundering"):
        return "sanctioned"
    return "intermediate"


def _evm_address_portable(address: str, source_chain: str) -> list[str]:
    """Return all EVM chains where this address is portable."""
    if source_chain not in _EVM_CHAINS:
        return []
    return [c for c in ["ethereum", "bsc", "polygon", "arbitrum", "optimism", "base", "avalanche"] if c != source_chain]


def _report_to_dict(report: CrossChainTraceReport) -> dict[str, Any]:
    """Convert CrossChainTraceReport to a plain dict for serialization."""
    return {
        "seed_address": report.seed_address,
        "seed_chain": report.seed_chain,
        "nodes": report.nodes,
        "edges": report.edges[:50],
        "bridge_hops": report.bridge_hops,
        "entity_flags": report.entity_flags,
        "known_entities": report.known_entities,
        "total_volume_received": report.total_volume_received,
        "total_volume_sent": report.total_volume_sent,
        "chains_involved": report.chains_involved,
        "unique_counterparties": report.unique_counterparties,
        "risk_score": report.risk_score,
        "risk_level": report.risk_level,
        "error": report.error,
    }
