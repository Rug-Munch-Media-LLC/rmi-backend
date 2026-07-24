"""
Smart Money Tracker — Types & Data Models
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class WalletCategory(Enum):
    EXCHANGE = "exchange"
    WHALE = "whale"
    VC = "vc"
    PROTOCOL = "protocol"
    MARKET_MAKER = "market_maker"
    TRADER = "trader"
    DEFI_USER = "defi_user"
    BRIDGE = "bridge"
    SUSPECT = "suspect"
    UNKNOWN = "unknown"


def safe_wallet_category(value: str) -> WalletCategory:
    try:
        return WalletCategory(value.lower())
    except (ValueError, AttributeError):
        return WalletCategory.UNKNOWN


class MoveDirection(Enum):
    ENTER = "enter"
    EXIT = "exit"
    ADD = "add"
    REDUCE = "reduce"
    TRANSFER = "transfer"
    INTERACT = "interact"
    BRIDGE = "bridge"
    SWAP = "swap"


class ImpactLevel(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class SmartWallet:
    address: str
    chain: str
    label: str = ""
    category: WalletCategory = WalletCategory.UNKNOWN
    tags: set[str] = field(default_factory=set)
    first_seen: float = 0.0
    last_active: float = 0.0
    total_tx_count: int = 0
    reputation_score: float = 50.0
    confidence: float = 0.5
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.address.lower()}"

    @property
    def reputation(self) -> float:
        return self.reputation_score

    def summary(self) -> str:
        return f"[{self.chain.upper()}] {self.label or self.address[:12]} ({self.category.value})"


@dataclass
class DetectedMove:
    wallet: SmartWallet | None = None
    token_address: str = ""
    token_symbol: str = ""
    chain: str = ""
    direction: MoveDirection = MoveDirection.INTERACT
    tx_hash: str = ""
    timestamp: datetime | None = None
    amount: float = 0.0
    value_usd: float = 0.0
    impact: ImpactLevel = ImpactLevel.INFO
    token_safety_score: float = 50.0
    confidence: float = 0.5
    context: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        wallet_name = self.wallet.label if self.wallet else "Unknown"
        return (
            f"{self.direction.value.upper():8s} | "
            f"{self.token_symbol:8s} | "
            f"${self.value_usd:>10,.0f} | "
            f"{self.impact.value.upper():10s} | "
            f"{wallet_name}"
        )


@dataclass
class FlowReport:
    moves: list[DetectedMove] = field(default_factory=list)
    wallets_scanned: int = 0
    chains_covered: list[str] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def top_moves(self, limit: int = 10) -> list[DetectedMove]:
        return sorted(self.moves, key=lambda m: m.value_usd, reverse=True)[:limit]

    def by_impact(self, level: ImpactLevel) -> list[DetectedMove]:
        return [m for m in self.moves if m.impact == level]

    def summary(self) -> str:
        critical = len(self.by_impact(ImpactLevel.CRITICAL))
        high = len(self.by_impact(ImpactLevel.HIGH))
        medium = len(self.by_impact(ImpactLevel.MEDIUM))
        total_value = sum(m.value_usd for m in self.moves)
        return (
            f"Smart Money Flow Report\n"
            f"{'=' * 40}\n"
            f"Wallets scanned: {self.wallets_scanned}\n"
            f"Chains: {', '.join(self.chains_covered) or 'N/A'}\n"
            f"Total moves: {len(self.moves)}\n"
            f"  Critical: {critical}\n"
            f"  High:     {high}\n"
            f"  Medium:   {medium}\n"
            f"Total volume: ${total_value:,.0f}\n"
            f"Scan time: {self.scan_duration_ms:.0f}ms\n"
            f"Top moves:\n" + "\n".join(
                f"  {m.summary()}" for m in self.top_moves(5)
            )
        )
