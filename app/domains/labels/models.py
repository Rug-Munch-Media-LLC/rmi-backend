"""Label data models for the federated label API.

A Label represents one piece of information about an address from one
source. Multiple labels from different sources for the same address
get merged into a unified view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

class LabelSource(StrEnum):
    """Which source a label came from. Used for provenance + dedup."""

    ETH_LABELS_DB = "eth-labels-db"  # local SQLite, 115K EVM labels
    ETHERSCAN_CSV = "etherscan-combined"  # local CSV, 51K EVM labels
    ETHEREUM_LABELS_CSV = "ethereum-labels"  # local CSV, 51K EVM labels
    SOLANA_LABELS_CSV = "solana-labels"  # local CSV, 103K SOL labels
    CLICKHOUSE = "clickhouse-wallet-memory"  # live CH wallet_labels table
    METASLEUTH = "metasleuth"  # BlockSec AML API
    OPEN_LABELS = "open-labels-initiative"  # OLI REST API (TBD)
    CHAINBASE = "chainbase"  # Chainbase API (TBD, needs key)
    INTERNAL = "internal"  # our own growing set
    MBAL = "mbal"  # Multi-blockchain Anti-Laundering source

@dataclass
class Label:
    """A single label for an address.

    Multiple labels can exist for the same (address, chain, label_type)
    from different sources. Dedup logic keeps the highest-confidence one.
    """

    address: str
    chain: str  # "ethereum" | "solana" | "base" | "bitcoin" | etc.
    label_type: str  # "entity" | "tag" | "category" | "risk" | "custom"
    label_value: str  # "Binance" | "Exchange" | "Sanctioned" | etc.
    source: LabelSource
    confidence: float = 0.5  # 0.0-1.0
    verified_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attributes: dict[str, Any] = field(default_factory=dict)
    # Optional entity info (for MetaSleuth etc.)
    entity: str | None = None
    entity_category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses."""
        return {
            "address": self.address,
            "chain": self.chain,
            "label_type": self.label_type,
            "label_value": self.label_value,
            "source": self.source.value,
            "confidence": self.confidence,
            "verified_at": self.verified_at.isoformat(),
            "attributes": self.attributes,
            "entity": self.entity,
            "entity_category": self.entity_category,
        }

    @classmethod
    def dedupe_key(
        cls, address: str, chain: str, label_type: str, label_value: str
    ) -> tuple[str, str, str, str]:
        """Generate dedup key. Same key = same label from different sources."""
        return (address.lower(), chain.lower(), label_type.lower(), label_value.lower())

    @classmethod
    def merge(cls, labels: list[Label]) -> Label:
        """Merge multiple labels with same dedup_key into one.

        Keeps highest confidence, unions attributes, aggregates sources.
        """
        if not labels:
            raise ValueError("Cannot merge empty label list")
        # Sort by confidence desc, take best as base
        sorted_labels = sorted(labels, key=lambda l: -l.confidence)  # noqa: E741
        base = sorted_labels[0]
        sources = list({l.source for l in labels})  # noqa: E741
        merged_attrs = dict(base.attributes)
        for l in labels[1:]:  # noqa: E741
            merged_attrs.update(l.attributes)
        # Update verified_at to most recent
        verified_at = max(l.verified_at for l in labels)  # noqa: E741
        return cls(
            address=base.address,
            chain=base.chain,
            label_type=base.label_type,
            label_value=base.label_value,
            source=base.source,  # keep highest-confidence source as primary
            confidence=base.confidence,
            verified_at=verified_at,
            attributes={
                **merged_attrs,
                "sources_count": len(sources),
                "all_sources": [s.value for s in sources],
            },
            entity=base.entity,
            entity_category=base.entity_category,
        )
