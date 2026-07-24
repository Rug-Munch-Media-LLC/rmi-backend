"""
Data models for Flash Loan Attack Detection
============================================
Enums, dataclasses, and data transfer objects used by the flash loan
attack detection engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class FlashLoanProtocol(Enum):
    """Supported flash loan protocols."""

    AAVE_V2 = "aave_v2"
    AAVE_V3 = "aave_v3"
    DYDX = "dydx"
    UNISWAP_V3 = "uniswap_v3"
    BALANCER = "balancer"
    EULER = "euler"
    RADIANT = "radiant"
    SPARK = "spark"
    MAKER = "maker"
    MORPHO = "morpho"
    SILO = "silo"
    UNKNOWN = "unknown"

class AttackType(Enum):
    """Classification of flash loan-based attacks."""

    PRICE_ORACLE_MANIPULATION = "price_oracle_manipulation"
    LP_DRAIN = "lp_drain"
    ARBITRAGE = "arbitrage"
    GOVERNANCE_ATTACK = "governance_attack"
    LIQUIDATION_MANIPULATION = "liquidation_manipulation"
    CROSS_PROTOCOL_CHAIN = "cross_protocol_chain"
    SELF_LIQUIDATION = "self_liquidation"
    REENTRANCY_EXPLOIT = "reentrancy_exploit"
    BORROW_MANIPULATION = "borrow_manipulation"
    SYNTHETIC_POSITION = "synthetic_position"
    UNKNOWN = "unknown"

class AttackSeverity(Enum):
    """Severity of the detected flash loan attack."""

    CRITICAL = "critical"  # >$1M loss or systemic protocol risk
    HIGH = "high"  # $100K-$1M loss
    MEDIUM = "medium"  # $10K-$100K loss
    LOW = "low"  # <$10K loss
    INFO = "info"  # Flash loan detected but no attack confirmed

class DetectionMethod(Enum):
    """How the flash loan attack was detected."""

    DIRECT_CALL = "direct_call"  # Matched protocol function signature
    PROVIDER_ADDRESS = "provider_address"  # Known flash loan provider called
    LIFECYCLE_PATTERN = "lifecycle_pattern"  # Borrow + manipulate + repay pattern
    PROFIT_EXTRACTION = "profit_extraction"  # Attacker made profit from flash loan
    ORACLE_DEVIATION = "oracle_deviation"  # Price moved during flash loan window
    MULTIPLE_CALLS = "multiple_calls"  # Multiple protocol calls in one tx

@dataclass
class FlashLoanCall:
    """A single flash loan call detected in a transaction."""

    protocol: FlashLoanProtocol
    provider_address: str
    chain: str
    block_number: int
    tx_index: int

    # Loan details
    token_address: str = ""
    token_symbol: str = ""
    amount_borrowed_raw: str = "0"
    amount_borrowed_usd: float = 0.0
    amount_repaid_raw: str = "0"
    amount_repaid_usd: float = 0.0
    fee_paid_raw: str = "0"
    fee_paid_usd: float = 0.0

    # Timing
    call_position: int = 0  # Position in the transaction trace
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        """Basic field validation."""
        if self.amount_borrowed_usd < 0:
            raise ValueError(f"Negative borrow amount: {self.amount_borrowed_usd}")
        if self.amount_repaid_usd < 0:
            raise ValueError(f"Negative repay amount: {self.amount_repaid_usd}")
        if self.block_number < 0:
            raise ValueError(f"Negative block number: {self.block_number}")

    def profit_usd(self) -> float:
        """Net profit from this flash loan (positive = attack profitable)."""
        return self.amount_borrowed_usd - self.amount_repaid_usd

    def summary(self) -> str:
        """One-line summary."""
        return (
            f"{self.protocol.value.upper()} flash loan | "
            f"${self.amount_borrowed_usd:,.0f} borrowed | "
            f"Fee: ${self.fee_paid_usd:,.0f} | "
            f"{self.token_symbol or self.token_address[:10]} | "
            f"Block #{self.block_number}"
        )

@dataclass
class TransactionTrace:
    """Simplified internal transaction trace for flash loan analysis."""

    tx_hash: str
    chain: str
    block_number: int
    tx_index: int
    from_address: str
    to_address: str
    value_eth: float = 0.0
    gas_price_gwei: float = 0.0
    gas_used: int = 0
    input_data: str = ""
    internal_calls: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)

    def has_flashloan_sig(self) -> bool:
        """Check if tx input data starts with a known flash loan signature."""
        # Lazy import to avoid circular dependency with algorithms
        from app.detectors.flash_loan.algorithms import FLASHLOAN_SIGNATURES  # fmt: skip

        if not self.input_data or len(self.input_data) < 10:
            return False
        sig = self.input_data[:10].lower()
        return sig in FLASHLOAN_SIGNATURES

    def flashloan_protocol(self) -> FlashLoanProtocol | None:
        """Identify the flash loan protocol from signature + provider address.

        ⚠️ Multiple protocols share signature 0xab9c4b5d (Aave V2, Aave V3 Simple,
        Silo Finance). When this shared sig is detected, the provider address
        (self.to_address) is checked against the known provider list for
        disambiguation.
        """
        # Lazy import to avoid circular dependency with algorithms
        from app.detectors.flash_loan.algorithms import (
            FLASHLOAN_SIGNATURES,
            SHARED_AAVE_FLASHLOAN_SIG,
            _resolve_shared_aave_sig,
        )

        if not self.input_data or len(self.input_data) < 10:
            return None
        sig = self.input_data[:10].lower()

        if sig == SHARED_AAVE_FLASHLOAN_SIG:
            return _resolve_shared_aave_sig(self.to_address, self.chain)

        protocol_name = FLASHLOAN_SIGNATURES.get(sig)
        if protocol_name:
            try:
                return FlashLoanProtocol(protocol_name)
            except ValueError:
                return FlashLoanProtocol.UNKNOWN
        return None

@dataclass
class FlashLoanAttack:
    """A complete detected flash loan attack with full context."""

    # Core identification
    chain: str
    block_number: int
    tx_hash: str
    attacker_address: str

    # Attack details
    attack_type: AttackType = AttackType.UNKNOWN
    protocol_used: FlashLoanProtocol = FlashLoanProtocol.UNKNOWN
    detection_methods: list[DetectionMethod] = field(default_factory=list)

    # Financial impact
    flash_loan_calls: list[FlashLoanCall] = field(default_factory=list)
    total_borrowed_usd: float = 0.0
    total_repaid_usd: float = 0.0
    attacker_profit_usd: float = 0.0
    victim_loss_usd: float = 0.0
    protocol_loss_usd: float = 0.0

    # Classification
    severity: AttackSeverity = AttackSeverity.INFO
    confidence: float = 0.0  # 0.0-1.0
    sophistication_score: float = 0.0  # 0.0-10.0

    # Context
    targets: list[str] = field(default_factory=list)  # Target contract addresses
    tokens_involved: list[str] = field(default_factory=list)
    exploit_timeline: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # Metadata
    detected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    verified: bool = False

    def __post_init__(self) -> None:
        """Validate and compute derived fields."""
        if self.total_borrowed_usd < 0:
            raise ValueError(f"Negative total borrowed: {self.total_borrowed_usd}")
        if self.attacker_profit_usd < 0:
            raise ValueError(f"Negative attacker profit: {self.attacker_profit_usd}")

    def summary(self) -> str:
        """Human-readable one-line attack summary."""
        attack_label = self.attack_type.value.replace("_", " ").title()
        protocol_label = self.protocol_used.value.upper()
        return (
            f"[{self.severity.value.upper()}] FLASH LOAN ATTACK | "
            f"{attack_label} via {protocol_label} | "
            f"${self.attacker_profit_usd:,.0f} profit | "
            f"${self.victim_loss_usd:,.0f} loss | "
            f"Attacker: {self.attacker_address[:12]} | "
            f"Block #{self.block_number} ({self.chain})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializable dict for API responses and alert pipeline."""
        return {
            "type": "flash_loan_attack",
            "chain": self.chain,
            "block": self.block_number,
            "tx_hash": self.tx_hash,
            "attacker": self.attacker_address,
            "attack_type": self.attack_type.value,
            "protocol": self.protocol_used.value,
            "severity": self.severity.value,
            "confidence": round(self.confidence, 2),
            "sophistication": round(self.sophistication_score, 1),
            "total_borrowed_usd": round(self.total_borrowed_usd, 2),
            "attacker_profit_usd": round(self.attacker_profit_usd, 2),
            "victim_loss_usd": round(self.victim_loss_usd, 2),
            "protocol_loss_usd": round(self.protocol_loss_usd, 2),
            "targets": self.targets,
            "tokens": self.tokens_involved,
            "tags": self.tags,
            "detected_at": self.detected_at,
            "verified": self.verified,
            "flash_loans": [
                {
                    "protocol": fl.protocol.value,
                    "token": fl.token_symbol or fl.token_address[:10],
                    "amount_borrowed_usd": round(fl.amount_borrowed_usd, 2),
                    "amount_repaid_usd": round(fl.amount_repaid_usd, 2),
                }
                for fl in self.flash_loan_calls
            ],
            "timeline": self.exploit_timeline,
        }

@dataclass
class ScanReport:
    """Complete scan report with all findings and metadata."""

    chains_scanned: list[str] = field(default_factory=list)
    blocks_scanned: int = 0
    transactions_analyzed: int = 0
    flash_loans_detected: int = 0
    attacks_confirmed: list[FlashLoanAttack] = field(default_factory=list)
    suspicious_transactions: list[dict[str, Any]] = field(default_factory=list)
    vulnerable_protocols: list[dict[str, Any]] = field(default_factory=list)

    scan_start: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    scan_end: str = ""
    duration_seconds: float = 0.0

    @property
    def total_attack_loss_usd(self) -> float:
        """Total financial loss across all confirmed attacks."""
        return sum(a.victim_loss_usd + a.protocol_loss_usd for a in self.attacks_confirmed)

    @property
    def total_attacker_profit_usd(self) -> float:
        """Total attacker profit across all confirmed attacks."""
        return sum(a.attacker_profit_usd for a in self.attacks_confirmed)

    @property
    def critical_attacks(self) -> list[FlashLoanAttack]:
        """Filter only critical severity attacks."""
        return [a for a in self.attacks_confirmed if a.severity == AttackSeverity.CRITICAL]

    def top_attacks(self, limit: int = 10) -> list[FlashLoanAttack]:
        """Return the most financially impactful attacks."""
        sorted_attacks = sorted(
            self.attacks_confirmed,
            key=lambda a: a.attacker_profit_usd + a.victim_loss_usd,
            reverse=True,
        )
        return sorted_attacks[:limit]

    def summary(self) -> str:
        """High-level summary of scan results."""
        total_loss = self.total_attack_loss_usd
        profit = self.total_attacker_profit_usd
        n_crit = len(self.critical_attacks)
        return (
            f"Flash Loan Scan Report\n"
            f"  Chains: {len(self.chains_scanned)} | "
            f"Blocks: {self.blocks_scanned} | "
            f"Tx Analyzed: {self.transactions_analyzed}\n"
            f"  Flash Loans: {self.flash_loans_detected} | "
            f"Attacks: {len(self.attacks_confirmed)} | "
            f"Critical: {n_crit}\n"
            f"  Total Loss: ${total_loss:,.0f} | "
            f"Attacker Profit: ${profit:,.0f}"
        )
