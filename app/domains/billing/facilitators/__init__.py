"""
x402 Multi-Facilitator Registry
================================
Pluggable facilitator modules for payment verification and settlement.
Each facilitator implements the base.Facilitator interface.

Architecture:
    Payment Request → Smart Router → Best Facilitator → Verify/Settle
                                                   ↓ (fallback)
                                              Next Facilitator

Supported Facilitators:
    Hosted:
        - Coinbase CDP (Base, instant settlement)
        - PayAI (Base, Solana, deferred)
        - Cloudflare x402 (Base, Ethereum)
        - AsterPay (EUR/SEPA, European)
        - Primev FastRPC (Ethereum, fee-free, mev-commit)
    Self-Hosted:
        - x402-rs (multi-chain Rust facilitator)
    Universal:
        - EIP-7702 (all EVM chains, all tokens, all native coins)
    OFFLINE (dead - DNS NXDOMAIN):
        - BNB Pieverse (api.pieverse.xyz) - OFFLINE
        - MERX x402 (api.merx.finance) - OFFLINE
        - Satoshi (api.satoshi.dev) - OFFLINE
"""

from __future__ import annotations

from app.domains.billing.facilitators.base import (
    Facilitator,
    FacilitatorRegistry,
    SettlementResult,
    VerificationResult,
)
from app.domains.billing.facilitators.router import FacilitatorRouter, get_facilitator_router

__all__ = [
    "Facilitator",
    "FacilitatorRegistry",
    "FacilitatorRouter",
    "SettlementResult",
    "VerificationResult",
    "get_facilitator_router",
]
