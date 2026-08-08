"""Wallet Monitor — Data Models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class WalletTx:
    hash: str
    from_addr: str
    to_addr: str
    value: float
    chain: str
    timestamp: str
    gas_price: float | None = None
    token_transfers: list[dict[str, Any]] = field(default_factory=list)
    is_contract_interaction: bool = False
    is_contract_creation: bool = False
    is_approval: bool = False
    dex_swap: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hash": self.hash,
            "from": self.from_addr,
            "to": self.to_addr,
            "value": self.value,
            "chain": self.chain,
            "timestamp": self.timestamp,
            "is_approval": self.is_approval,
            "is_contract_interaction": self.is_contract_interaction,
            "is_contract_creation": self.is_contract_creation,
            "dex_swap": self.dex_swap,
            "token_transfers": self.token_transfers,
        }


@dataclass
class WalletAlert:
    alert_type: str
    wallet: str
    chain: str
    tx_hash: str
    severity: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "wallet_alert",
            "alert_type": self.alert_type,
            "wallet": self.wallet,
            "chain": self.chain,
            "tx_hash": self.tx_hash,
            "severity": self.severity,
            "message": self.message,
            "metadata": self.metadata,
            "timestamp": self.timestamp or datetime.now(UTC).isoformat(),
        }


@dataclass
class BurstSignal:
    wallet: str
    chain: str
    tx_count: int
    window_seconds: int
    severity: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "burst",
            "wallet": self.wallet,
            "chain": self.chain,
            "tx_count": self.tx_count,
            "window_seconds": self.window_seconds,
            "severity": self.severity,
            "timestamp": self.timestamp,
        }


@dataclass
class LabelSuggestion:
    wallet: str
    chain: str
    suggested_label: str
    confidence: float
    reasoning: str
    pattern_summary: dict[str, Any]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "label_suggestion",
            "wallet": self.wallet,
            "chain": self.chain,
            "suggested_label": self.suggested_label,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "pattern_summary": self.pattern_summary,
            "timestamp": self.timestamp,
        }
