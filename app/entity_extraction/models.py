"""
Entity Extraction - Data Models
================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EntityExtractionResult:
    """Result of extracting entities from a text blob."""

    evm_addresses: list[str] = field(default_factory=list)
    solana_addresses: list[str] = field(default_factory=list)
    token_symbols: list[str] = field(default_factory=list)
    chain_names: list[str] = field(default_factory=list)
    protocol_names: list[str] = field(default_factory=list)
    scam_keywords: list[str] = field(default_factory=list)
    tx_hashes: list[str] = field(default_factory=list)
    ens_domains: list[str] = field(default_factory=list)

    @property
    def has_entities(self) -> bool:
        return bool(
            self.evm_addresses or self.solana_addresses or self.token_symbols
            or self.chain_names or self.protocol_names or self.scam_keywords
            or self.tx_hashes or self.ens_domains
        )

    @property
    def total_count(self) -> int:
        return (
            len(self.evm_addresses) + len(self.solana_addresses)
            + len(self.token_symbols) + len(self.chain_names)
            + len(self.protocol_names) + len(self.scam_keywords)
            + len(self.tx_hashes) + len(self.ens_domains)
        )

    def all_entities(self) -> list[dict[str, str]]:
        """Flatten into a list of {type, value} dicts (normalised)."""
        out: list[dict[str, str]] = []
        for v in self.evm_addresses:
            out.append({"type": "evm_address", "value": v.lower()})
        for v in self.solana_addresses:
            out.append({"type": "solana_address", "value": v})
        for v in self.token_symbols:
            out.append({"type": "token_symbol", "value": v.upper()})
        for v in self.chain_names:
            out.append({"type": "chain_name", "value": v.lower()})
        for v in self.protocol_names:
            out.append({"type": "protocol_name", "value": v.lower()})
        for v in self.scam_keywords:
            out.append({"type": "scam_keyword", "value": v.lower()})
        for v in self.tx_hashes:
            out.append({"type": "tx_hash", "value": v.lower()})
        for v in self.ens_domains:
            out.append({"type": "ens_domain", "value": v.lower()})
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "evm_addresses": self.evm_addresses,
            "solana_addresses": self.solana_addresses,
            "token_symbols": self.token_symbols,
            "chain_names": self.chain_names,
            "protocol_names": self.protocol_names,
            "scam_keywords": self.scam_keywords,
            "tx_hashes": self.tx_hashes,
            "ens_domains": self.ens_domains,
            "has_entities": self.has_entities,
            "total_count": self.total_count,
        }
