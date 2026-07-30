"""
Wallet clustering data models.

Defines Transaction, WalletProfile, Cluster, and Connection dataclasses
used by all clustering detection methods and visualization.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class Transaction:
    """Represents a blockchain transaction."""

    signature: str
    timestamp: datetime
    from_address: str
    to_address: str
    amount: float
    token: str
    program: str
    success: bool = True

    @property
    def is_transfer(self) -> bool:
        return self.program in ["spl-token", "system", "transfer"]


@dataclass
class WalletProfile:
    """Profile of a wallet's behavior."""

    address: str
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    total_transactions: int = 0
    total_volume: float = 0.0
    unique_counterparties: set[str] = field(default_factory=set)
    token_holdings: dict[str, float] = field(default_factory=dict)
    transaction_times: list[datetime] = field(default_factory=list)
    programs_used: set[str] = field(default_factory=set)

    avg_transaction_size: float = 0.0
    transaction_frequency: float = 0.0
    preferred_hours: list[int] = field(default_factory=list)

    def calculate_metrics(self) -> None:
        """Calculate behavioral metrics from transaction data."""
        if self.transaction_times:
            self.transaction_times.sort()
            self.first_seen = self.transaction_times[0]
            self.last_seen = self.transaction_times[-1]

            days_active = (self.last_seen - self.first_seen).days + 1
            if days_active > 0:
                self.transaction_frequency = len(self.transaction_times) / days_active

            hours = [t.hour for t in self.transaction_times]
            hour_counts = defaultdict(int)
            for h in hours:
                hour_counts[h] += 1
            self.preferred_hours = sorted(hour_counts.keys(), key=lambda x: hour_counts[x], reverse=True)[:3]


@dataclass
class Cluster:
    """A detected wallet cluster."""

    cluster_id: str
    wallets: set[str]
    confidence: float
    detection_methods: list[str]
    center_wallet: str | None = None
    total_volume: float = 0.0
    common_tokens: set[str] = field(default_factory=set)
    common_counterparties: set[str] = field(default_factory=set)
    first_activity: datetime | None = None
    last_activity: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "wallets": list(self.wallets),
            "wallet_count": len(self.wallets),
            "confidence": round(self.confidence, 3),
            "detection_methods": self.detection_methods,
            "center_wallet": self.center_wallet,
            "total_volume": self.total_volume,
            "common_tokens": list(self.common_tokens),
            "common_counterparties": list(self.common_counterparties),
            "first_activity": self.first_activity.isoformat() if self.first_activity else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
        }


@dataclass
class Connection:
    """Connection between two wallets."""

    wallet_a: str
    wallet_b: str
    strength: float
    connection_types: list[str]
    evidence: list[dict]
    total_volume: float = 0.0
    transaction_count: int = 0
    first_connection: datetime | None = None
    last_connection: datetime | None = None


__all__ = ["Cluster", "Connection", "Transaction", "WalletProfile"]
