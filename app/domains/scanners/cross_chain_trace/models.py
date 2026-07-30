"""
Cross-Chain Trace — Data Models
================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CrossChainNode:
    """A wallet address discovered in the fund flow trace."""

    address: str
    chain: str
    label: str | None
    label_category: str | None
    role: str
    total_received: float = 0.0
    total_sent: float = 0.0
    tx_count: int = 0
    is_sanctioned: bool = False


@dataclass
class CrossChainEdge:
    """A token transfer between two addresses."""

    from_address: str
    to_address: str
    chain: str
    token_address: str
    token_symbol: str | None
    value: str
    tx_hash: str
    block_number: int
    block_timestamp: str
    is_bridge: bool = False
    bridge_destination_chain: str | None = None


@dataclass
class CrossChainTraceReport:
    """
    Complete cross-chain fund flow trace for a wallet address.
    Used as the module output — compatible with SentinelReport field storage.
    """

    seed_address: str
    seed_chain: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    bridge_hops: list[dict[str, Any]] = field(default_factory=list)
    entity_flags: list[str] = field(default_factory=list)
    known_entities: dict[str, str] = field(default_factory=dict)
    total_volume_received: float = 0.0
    total_volume_sent: float = 0.0
    chains_involved: list[str] = field(default_factory=list)
    unique_counterparties: int = 0
    risk_score: float = 0.0
    risk_level: str = "low"
    error: str | None = None
