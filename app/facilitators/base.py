"""
x402 Facilitator Base Class
============================
Abstract base for all x402 payment facilitators.
Each facilitator implements verify(), settle(), and health().

Protocol:
    - verify(payload, requirements) → VerificationResult
    - settle(payment_data) → SettlementResult
    - health() → bool

Supporting types:
    - VerificationResult: verified, reason, tx_hash, payer, amount, chain
    - SettlementResult: settled, settlement_id, tx_hash, block_number
    - TokenInfo: token address, name, decimals, chain
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("facilitator_base")


class FacilitatorType(Enum):
    """Type of facilitator - hosted (external) or self-hosted."""

    HOSTED = "hosted"
    SELF_HOSTED = "self_hosted"


class SettlementType(Enum):
    """How the facilitator settles payments."""

    INSTANT = "instant"  # On-chain settlement within seconds
    DEFERRED = "deferred"  # Batched settlement later
    PRECONFIRMATION = "preconf"  # mev-commit preconfirmations (Primev)
    FEE_FREE = "fee_free"  # No settlement fee (Primev)
    OFF_RAMP = "off_ramp"  # Fiat conversion (AsterPay EUR/SEPA)


@dataclass
class NetworkSupport:
    """Which chains and tokens a facilitator supports."""

    chains: list[str] = field(default_factory=list)  # e.g. ["base", "solana", "ethereum"]
    chain_ids: list[int] = field(default_factory=list)  # e.g. [8453, 1]
    networks: list[str] = field(default_factory=list)  # e.g. ["eip155:8453", "solana:..."]
    tokens: list[str] = field(default_factory=list)  # e.g. ["USDC", "USDT", "USDD"]
    native_tokens: list[str] = field(default_factory=list)  # e.g. ["ETH", "SOL", "TRX"]
    token_addresses: dict[str, str] = field(default_factory=dict)  # symbol → address by chain


@dataclass
class VerificationResult:
    """Result of payment verification."""

    verified: bool
    reason: str = ""
    tx_hash: str | None = None
    settlement_id: str | None = None
    payer: str | None = None
    amount: str | None = None  # in atomic units
    amount_usd: float | None = None
    chain: str | None = None
    token: str | None = None  # USDC, USDT, etc.
    facilitator: str | None = None
    block_number: int | None = None
    confirmations: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "verified": self.verified,
            "reason": self.reason,
            "tx_hash": self.tx_hash,
            "settlement_id": self.settlement_id,
            "payer": self.payer,
            "amount": self.amount,
            "amount_usd": self.amount_usd,
            "chain": self.chain,
            "token": self.token,
            "facilitator": self.facilitator,
            "block_number": self.block_number,
            "confirmations": self.confirmations,
            "extra": self.extra,
        }


@dataclass
class SettlementResult:
    """Result of settlement execution."""

    settled: bool
    settlement_id: str | None = None
    tx_hash: str | None = None
    block_number: int | None = None
    chain: str | None = None
    facilitator: str | None = None
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "settled": self.settled,
            "settlement_id": self.settlement_id,
            "tx_hash": self.tx_hash,
            "block_number": self.block_number,
            "chain": self.chain,
            "facilitator": self.facilitator,
            "reason": self.reason,
            "extra": self.extra,
        }


class Facilitator(ABC):
    """
    Abstract base for all x402 payment facilitators.

    Subclasses must implement:
        - name (property): Unique facilitator name
        - facilitator_type (property): HOSTED or SELF_HOSTED
        - settlement_type (property): INSTANT, DEFERRED, etc.
        - supported_networks (property): What chains/tokens this supports
        - verify(payload, requirements): Verify a payment
        - health(): Check if facilitator is online
        - settle(payment_data): Settle a verified payment (optional default: no-op)
    """

    # ── Identity ───────────────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique facilitator name (e.g. 'payai', 'coinbase_cdp')."""
        ...

    @property
    @abstractmethod
    def facilitator_type(self) -> FacilitatorType:
        """HOSTED or SELF_HOSTED."""
        ...

    @property
    @abstractmethod
    def settlement_type(self) -> SettlementType:
        """How this facilitator settles."""
        ...

    @property
    @abstractmethod
    def supported_networks(self) -> NetworkSupport:
        """Which chains, tokens, and networks this supports."""
        ...

    # ── Metadata ───────────────────────────────────────────────

    @property
    def description(self) -> str:
        return f"{self.name} - {self.facilitator_type.value} - {self.settlement_type.value}"

    @property
    def priority(self) -> int:
        """Lower = higher priority in routing. Default: 50."""
        return 50

    @property
    def is_fee_free(self) -> bool:
        return self.settlement_type == SettlementType.FEE_FREE

    @property
    def verify_url(self) -> str | None:
        """URL for verification endpoint (hosted facilitators)."""
        return None

    @property
    def settle_url(self) -> str | None:
        """URL for settlement endpoint (hosted facilitators)."""
        return None

    # ── Core Methods ───────────────────────────────────────────

    @abstractmethod
    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """
        Verify a payment payload against this facilitator.

        Args:
            payload: x402 payment payload (parsed JSON)
            requirements: Payment requirements with resource, accepts, etc.

        Returns:
            VerificationResult with verified status and details.
        """
        ...

    @abstractmethod
    async def health(self) -> bool:
        """Check if this facilitator is reachable and operational."""
        ...

    async def settle(
        self,
        payment_data: dict[str, Any],
    ) -> SettlementResult:
        """
        Settle a verified payment on-chain (or queue for batch).

        Default: no-op - many hosted facilitators settle automatically.
        Override for facilitators that need explicit settlement calls.
        """
        return SettlementResult(
            settled=True,
            reason="Hosted facilitator - auto-settled",
            facilitator=self.name,
        )

    # ── Helpers ────────────────────────────────────────────────

    def supports_chain(self, chain_key: str) -> bool:
        """Check if this facilitator supports a given chain key."""
        ns = self.supported_networks
        return chain_key.lower() in [c.lower() for c in ns.chains]

    def supports_token(self, token_symbol: str, chain_key: str | None = None) -> bool:
        """Check if this facilitator supports a token, optionally on a specific chain."""
        ns = self.supported_networks
        if token_symbol.upper() not in [t.upper() for t in ns.tokens]:
            return False
        return not (chain_key and chain_key.lower() not in [c.lower() for c in ns.chains])

    def _format_error(self, reason: str, **extra) -> VerificationResult:
        """Shortcut for failed verification results."""
        return VerificationResult(
            verified=False,
            reason=reason,
            facilitator=self.name,
            extra=extra,
        )

    def _format_success(
        self,
        tx_hash: str | None = None,
        payer: str | None = None,
        amount: str | None = None,
        amount_usd: float | None = None,
        chain: str | None = None,
        token: str = "USDC",
        settlement_id: str | None = None,
        block_number: int | None = None,
        confirmations: int | None = None,
        **extra,
    ) -> VerificationResult:
        """Shortcut for successful verification results."""
        return VerificationResult(
            verified=True,
            reason=f"Verified via {self.name}",
            tx_hash=tx_hash,
            settlement_id=settlement_id,
            payer=payer,
            amount=amount,
            amount_usd=amount_usd,
            chain=chain,
            token=token,
            facilitator=self.name,
            block_number=block_number,
            confirmations=confirmations,
            extra=extra,
        )


class FacilitatorRegistry:
    """
    Registry of all available facilitators.
    Loads facilitator instances and provides discovery/routing.

    Usage:
        registry = FacilitatorRegistry()
        registry.register(CoinbaseCDPFacilitator())
        registry.register(PayAIFacilitator())
        ...

        facilitators = registry.get_for_chain("base")
        result = await facilitators[0].verify(payload, requirements)
    """

    def __init__(self):
        self._facilitators: dict[str, Facilitator] = {}
        self._by_chain: dict[str, list[str]] = {}  # chain_key → [facilitator_names]

    def register(self, facilitator: Facilitator) -> None:
        """Register a facilitator instance."""
        self._facilitators[facilitator.name] = facilitator

        # Index by chain
        for chain in facilitator.supported_networks.chains:
            chain_lower = chain.lower()
            if chain_lower not in self._by_chain:
                self._by_chain[chain_lower] = []
            if facilitator.name not in self._by_chain[chain_lower]:
                self._by_chain[chain_lower].append(facilitator.name)

        logger.info(
            f"Registered facilitator: {facilitator.name} "
            f"({facilitator.facilitator_type.value}) "
            f"for chains: {facilitator.supported_networks.chains}"
        )

    def get(self, name: str) -> Facilitator | None:
        """Get a facilitator by name."""
        return self._facilitators.get(name)

    def get_for_chain(self, chain_key: str) -> list[Facilitator]:
        """Get all facilitators supporting a chain, sorted by priority."""
        chain_lower = chain_key.lower()
        names = self._by_chain.get(chain_lower, [])
        facilitators = [self._facilitators[n] for n in names if n in self._facilitators]
        facilitators.sort(key=lambda f: f.priority)
        return facilitators

    def get_all(self) -> list[Facilitator]:
        """Get all registered facilitators, sorted by priority."""
        return sorted(self._facilitators.values(), key=lambda f: f.priority)

    def get_healthy(self) -> list[Facilitator]:
        """Get all registered facilitators (health checked at call time)."""
        return self.get_all()

    @property
    def chain_coverage(self) -> dict[str, int]:
        """Number of facilitators per chain."""
        return {chain: len(names) for chain, names in self._by_chain.items()}

    @property
    def stats(self) -> dict[str, Any]:
        """Registry statistics."""
        all_f = self.get_all()
        return {
            "total_facilitators": len(all_f),
            "hosted": sum(1 for f in all_f if f.facilitator_type == FacilitatorType.HOSTED),
            "self_hosted": sum(1 for f in all_f if f.facilitator_type == FacilitatorType.SELF_HOSTED),
            "fee_free": sum(1 for f in all_f if f.is_fee_free),
            "chains_covered": list(self._by_chain.keys()),
            "chain_coverage": self.chain_coverage,
            "facilitators": [
                {
                    "name": f.name,
                    "type": f.facilitator_type.value,
                    "settlement": f.settlement_type.value,
                    "chains": f.supported_networks.chains,
                    "tokens": f.supported_networks.tokens,
                    "priority": f.priority,
                    "fee_free": f.is_fee_free,
                }
                for f in all_f
            ],
        }


# ── Global registry instance ────────────────────────────────────
# Imported and populated at startup from app startup handler

_registry: FacilitatorRegistry | None = None


def get_registry() -> FacilitatorRegistry:
    """Get or create the global facilitator registry."""
    global _registry
    if _registry is None:
        _registry = FacilitatorRegistry()
    return _registry


def reset_registry() -> None:
    """Reset the global registry (for testing)."""
    global _registry
    _registry = FacilitatorRegistry()
