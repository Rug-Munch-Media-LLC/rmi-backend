from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════
# Enums & Types
# ═══════════════════════════════════════════════════════════════


class WalletCategory(Enum):
    """Classification of smart money wallets."""

    WHALE = "whale"  # High-value wallet (>$1M)
    VC_FUND = "vc_fund"  # Known venture capital / investment fund
    PROTOCOL_TREASURY = "protocol_treasury"  # DeFi protocol treasury
    TOP_TRADER = "top_trader"  # Consistently profitable trader
    EARLY_ADOPTER = "early_adopter"  # Early position in winning projects
    DEVELOPER = "developer"  # Project developer / deployer
    EXCHANGE = "exchange"  # CEX/DEX hot wallet
    MARKET_MAKER = "market_maker"  # Market maker address
    SUSPECTED_INSIDER = "suspected_insider"  # Suspicious inside trader
    UNKNOWN = "unknown"


def safe_wallet_category(value: str) -> WalletCategory:
    """Safely convert a string to WalletCategory, falling back to UNKNOWN."""
    try:
        return WalletCategory(value)
    except (ValueError, AttributeError):
        return WalletCategory.UNKNOWN


class MoveDirection(Enum):
    """Direction of a wallet's position change."""

    ENTER = "enter"  # New position opened
    EXIT = "exit"  # Position fully closed
    ADD = "add"  # Increased position size
    REDUCE = "reduce"  # Decreased position size
    TRANSFER = "transfer"  # Moved tokens (could be wallet rotation)
    INTERACT = "interact"  # Contract interaction (stake, vote, etc.)


class ImpactLevel(Enum):
    """How impactful a detected move is."""

    CRITICAL = "critical"  # Massive position change by elite wallet
    HIGH = "high"  # Significant move by notable wallet
    MEDIUM = "medium"  # Moderate move or mid-tier wallet
    LOW = "low"  # Small move or low-confidence wallet
    INFO = "info"  # Informational, no strong signal


# ═══════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════


@dataclass
class SmartWallet:
    """Represents a tracked smart money wallet."""

    address: str
    chain: str
    label: str  # Human-readable name/alias
    category: WalletCategory
    tags: list[str] = field(default_factory=list)
    first_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_active: datetime = field(default_factory=lambda: datetime.now(UTC))
    total_value_usd: float = 0.0  # Estimated total portfolio value
    reputation_score: float = 50.0  # 0-100, higher = more reliable signal
    confidence: float = 0.5  # 0-1, how sure we are about this wallet
    tx_count: int = 0
    notes: str = ""

    def summary(self) -> str:
        cat_icon = {
            WalletCategory.WHALE: "🐋",
            WalletCategory.VC_FUND: "🏦",
            WalletCategory.PROTOCOL_TREASURY: "🏛️",
            WalletCategory.TOP_TRADER: "📈",
            WalletCategory.EARLY_ADOPTER: "🔮",
            WalletCategory.DEVELOPER: "👨‍💻",
            WalletCategory.EXCHANGE: "🔄",
            WalletCategory.MARKET_MAKER: "⚡",
            WalletCategory.SUSPECTED_INSIDER: "👁️",
            WalletCategory.UNKNOWN: "❓",
        }.get(self.category, "❓")
        addr_short = f"{self.address[:6]}...{self.address[-4:]}"
        return f"{cat_icon} **{self.label}** ({addr_short}) | {self.chain} | ${self.total_value_usd:,.0f} | Rep: {self.reputation_score:.0f}/100"


@dataclass
class DetectedMove:
    """A detected position change by a smart money wallet."""

    wallet: SmartWallet
    token_address: str
    token_symbol: str
    chain: str
    direction: MoveDirection
    tx_hash: str
    timestamp: datetime
    amount: float
    value_usd: float
    impact: ImpactLevel
    token_safety_score: float = 50.0  # From token scanner (0-100)
    confidence: float = 0.5
    context: str = ""  # e.g., "first buy after 6 months idle"
    details: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        dir_icon = {
            MoveDirection.ENTER: "🟢",
            MoveDirection.ADD: "📗",
            MoveDirection.EXIT: "🔴",
            MoveDirection.REDUCE: "📕",
            MoveDirection.TRANSFER: "🔄",
            MoveDirection.INTERACT: "⚙️",
        }.get(self.direction, "❓")
        impact_icon = {
            ImpactLevel.CRITICAL: "🚨",
            ImpactLevel.HIGH: "⚠️",
            ImpactLevel.MEDIUM: "📊",
            ImpactLevel.LOW: "📋",
            ImpactLevel.INFO: "💬",
        }.get(self.impact, "❓")
        return (
            f"{impact_icon}{dir_icon} {self.wallet.label} "
            f"**{self.direction.value.upper()}** ${self.token_symbol} "
            f"({self.value_usd:,.0f}) on {self.chain}\n"
            f"   Tx: `{self.tx_hash[:20]}...` | "
            f"Safety: {self.token_safety_score:.0f}/100 | "
            f"{self.context}"
        )


@dataclass
class FlowReport:
    """Complete report from a smart money scan."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    moves: list[DetectedMove] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    wallets_scanned: int = 0
    chains_covered: list[str] = field(default_factory=list)

    def top_moves(self, limit: int = 10) -> list[DetectedMove]:
        """Return highest-impact moves sorted by severity."""
        order = {
            ImpactLevel.CRITICAL: 0,
            ImpactLevel.HIGH: 1,
            ImpactLevel.MEDIUM: 2,
            ImpactLevel.LOW: 3,
            ImpactLevel.INFO: 4,
        }
        sorted_moves = sorted(self.moves, key=lambda m: (order.get(m.impact, 99), -m.value_usd))
        return sorted_moves[:limit]

    def by_impact(self, level: ImpactLevel) -> list[DetectedMove]:
        return [m for m in self.moves if m.impact == level]

    def summary(self) -> str:
        critical = len(self.by_impact(ImpactLevel.CRITICAL))
        high = len(self.by_impact(ImpactLevel.HIGH))
        medium = len(self.by_impact(ImpactLevel.MEDIUM))
        total_value = sum(m.value_usd for m in self.moves)
        top = self.top_moves(5)
        lines = [
            f"# Smart Money Flow Report - {self.timestamp.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            f"Scanned {self.wallets_scanned} wallets across {', '.join(self.chains_covered)} in {self.scan_duration_ms:.0f}ms",
            "",
            "## Summary",
            f"Total moves detected: {len(self.moves)} | Total value: ${total_value:,.0f}",
            f"  🚨 Critical: {critical} | ⚠️ High: {high} | 📊 Medium: {medium}",
            "",
            "## Top Moves",
        ]
        for i, move in enumerate(top, 1):
            lines.append("")
            lines.append(f"### {i}. {move.summary()}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Core Tracker
# ═══════════════════════════════════════════════════════════════
