"""
Detection Algorithms for Flash Loan Attack Detector
====================================================
Constants, signatures, provider addresses, and heuristic functions
for classifying flash loan attacks.

This module provides the stateless detection logic consumed by the
FlashLoanAttackDetector class in the parent package.
"""

from __future__ import annotations
from typing import Any

from app.detectors.flash_loan.models import (
    AttackSeverity,
    AttackType,
    DetectionMethod,
    FlashLoanCall,
    FlashLoanProtocol,
    TransactionTrace,
)

# ═══════════════════════════════════════════════════════════════════
# Constants - Known Flash Loan Protocol Signatures
# ═══════════════════════════════════════════════════════════════════

# Aave V2 flashLoan function signature
AAVE_V2_FLASHLOAN_SIG = "0xab9c4b5d"
# Aave V3 flashLoan (simple) signature
AAVE_V3_FLASHLOAN_SIG = "0x42b0b77c"
# Aave V3 flashLoanSimple signature
AAVE_V3_FLASHSIMPLE_SIG = "0xab9c4b5d"
# dYdX SoloMargin initiateFlashLoan
DYDX_FLASHLOAN_SIG = "0x5eb1b7c3"
# Uniswap V3 flash function
UNISWAP_V3_FLASH_SIG = "0x490e6c32"
# Balancer V2 flashLoan signature
BALANCER_FLASHLOAN_SIG = "0x52b0f4c1"
# Euler flashLoan
EULER_FLASHLOAN_SIG = "0xfc217659"
# Radiant flashLoan
RADIANT_FLASHLOAN_SIG = "0x7c5e9ea4"
# Spark flashLoan
SPARK_FLASHLOAN_SIG = "0x7d28c3b6"
# MakerDAO flash mint
MAKER_FLASHMINT_SIG = "0x606b0d3e"
# Morpho flashLoan
MORPHO_FLASHLOAN_SIG = "0x490e6c32"
# Silo Finance flashLoan
SILO_FLASHLOAN_SIG = "0xab9c4b5d"

# ⚠️ AAVE V2, AAVE V3 Simple, and Silo all share signature 0xab9c4b5d
# Disambiguation is done via provider address lookup, not just signature
SHARED_AAVE_FLASHLOAN_SIG = "0xab9c4b5d"

FLASHLOAN_SIGNATURES: dict[str, str] = {
    # Shared sig - protocol identified via provider address
    SHARED_AAVE_FLASHLOAN_SIG: "flash_loan",
    AAVE_V3_FLASHLOAN_SIG: "aave_v3",
    DYDX_FLASHLOAN_SIG: "dydx",
    UNISWAP_V3_FLASH_SIG: "uniswap_v3",
    BALANCER_FLASHLOAN_SIG: "balancer",
    EULER_FLASHLOAN_SIG: "euler",
    RADIANT_FLASHLOAN_SIG: "radiant",
    SPARK_FLASHLOAN_SIG: "spark",
    MAKER_FLASHMINT_SIG: "maker",
    MORPHO_FLASHLOAN_SIG: "morpho",
}

# Known flash loan provider addresses (simplified - in production these
# come from an on-chain registry / DataBus)
FLASHLOAN_PROVIDERS: dict[str, list[str]] = {
    "ethereum": [
        "0x7d2768De32b0b80b7a3454c06BdAc94A69DDc7A9",  # Aave V2 LendingPool
        "0x87870Bca3F3fD6335C3F4cE8392D69350B4fA4E2",  # Aave V3 Pool
        "0x1E0447b19BB6EcFdAe1e4AE1694b0C3659614e4e",  # dYdX SoloMargin
        "0xBA12222222228d8Ba445958a75a0704d566BF2C8",  # Balancer V2 Vault
        "0x27182842E098f60e3D576794A5bFFb0777E025d3",  # Euler
        "0xC13e21B648A5Ee7639021845b68d52BDAbe7C5c7",  # Radiant
        "0xC13e21B648A5Ee7639021845b68d52BDAbe7C5c8",  # Spark
        "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",  # Morpho Blue
    ],
    "bsc": [
        "0x87870Bca3F3fD6335C3F4cE8392D69350B4fA4E2",  # Aave V3 on BSC
        "0xBA12222222228d8Ba445958a75a0704d566BF2C8",  # Balancer V2 on BSC
        "0x1E0447b19BB6EcFdAe1e4AE1694b0C3659614e4e",  # dYdX on BSC
        "0x27182842E098f60e3D576794A5bFFb0777E025d3",  # Euler on BSC
        "0xC13e21B648A5Ee7639021845b68d52BDAbe7C5c7",  # Radiant on BSC
        "0xC13e21B648A5Ee7639021845b68d52BDAbe7C5c8",  # Spark on BSC
        "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",  # Morpho Blue on BSC
        "0x4983eDD22d41c72a752941b01Eb51ba76bDb5e9C",  # PancakeSwap MasterChef V3
    ],
    "arbitrum": [
        "0x794a61358D6845594F94dc1DB02A252b5b4814aD",  # Aave V3 on Arbitrum
        "0xBA12222222228d8Ba445958a75a0704d566BF2C8",  # Balancer V2 on Arbitrum
        "0x27182842E098f60e3D576794A5bFFb0777E025d3",  # Euler on Arbitrum
        "0xC13e21B648A5Ee7639021845b68d52BDAbe7C5c7",  # Radiant on Arbitrum
        "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",  # Morpho Blue on Arbitrum
    ],
    "base": [
        "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",  # Aave V3 on Base
        "0xBA12222222228d8Ba445958a75a0704d566BF2C8",  # Balancer V2 on Base
        "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",  # Morpho Blue on Base
    ],
    "polygon": [
        "0x794a61358D6845594F94dc1DB02A252b5b4814aD",  # Aave V3 on Polygon
        "0xBA12222222228d8Ba445958a75a0704d566BF2C8",  # Balancer V2 on Polygon
        "0x27182842E098f60e3D576794A5bFFb0777E025d3",  # Euler on Polygon
        "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",  # Morpho Blue on Polygon
    ],
    "optimism": [
        "0x794a61358D6845594F94dc1DB02A252b5b4814aD",  # Aave V3 on Optimism
        "0xBA12222222228d8Ba445958a75a0704d566BF2C8",  # Balancer V2 on Optimism
        "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",  # Morpho Blue on Optimism
    ],
    "avalanche": [
        "0x794a61358D6845594F94dc1DB02A252b5b4814aD",  # Aave V3 on Avalanche
        "0xBA12222222228d8Ba445958a75a0704d566BF2C8",  # Balancer V2 on Avalanche
    ],
    "fantom": [
        "0x794a61358D6845594F94dc1DB02A252b5b4814aD",  # Aave V3 on Fantom
        "0xBA12222222228d8Ba445958a75a0704d566BF2C8",  # Balancer V2 on Fantom
    ],
}

# ═══════════════════════════════════════════════════════════════════
# Attack Detection Heuristics
# ═══════════════════════════════════════════════════════════════════

# Patterns that suggest a flash loan was used for an attack
# instead of legitimate arbitrage or liquidation
_SUSPICIOUS_PATTERNS: list[dict[str, Any]] = [
    {"type": "price_impact", "description": "Significant price impact >5% in flash loan"},
    {"type": "multi_protocol", "description": "Multiple different protocols called in sequence"},
    {"type": "high_profit", "description": "Profit >$100K on flash loan", "threshold_usd": 100_000},
    {"type": "oracle_interaction", "description": "Oracle/price feed contracts called"},
    {"type": "governance_call", "description": "Governance contract interaction"},
    {"type": "contract_creation", "description": "New contract created during flash loan"},
    {"type": "nested_flashloan", "description": "Flash loan within a flash loan"},
]

# ═══════════════════════════════════════════════════════════════════
# Detection Functions
# ═══════════════════════════════════════════════════════════════════

def _detect_attack_type(
    flash_loans: list[FlashLoanCall],
    trace: TransactionTrace,
) -> tuple[AttackType, float]:
    """Classify the attack type based on flash loan and trace characteristics.

    Args:
        flash_loans: Detected flash loan calls in the transaction
        trace: Full transaction trace for context

    Returns:
        Tuple of (AttackType, confidence_score)
    """
    # Check for oracle manipulation - look for price feed addresses in call data
    oracle_patterns = [
        "price",
        "oracle",
        "twap",
        "chainlink",
        "aggregator",
        "getLatestPrice",
        "getRoundData",
        "peek",
        "latestAnswer",
    ]
    input_lower = trace.input_data.lower()

    oracle_hits = sum(1 for pat in oracle_patterns if pat in input_lower)

    # Check for LP interaction patterns
    lp_patterns = ["removeLiquidity", "withdraw", "burn", "collect", "swap"]
    lp_hits = sum(1 for pat in lp_patterns if pat in input_lower)

    # Check for governance patterns
    governance_patterns = ["propose", "vote", "queue", "execute", "castVote"]
    gov_hits = sum(1 for pat in governance_patterns if pat in input_lower)

    # Score each attack type
    scores: dict[AttackType, float] = {}

    if oracle_hits >= 2:
        scores[AttackType.PRICE_ORACLE_MANIPULATION] = min(0.9, 0.3 + oracle_hits * 0.2)

    if lp_hits >= 2 and len(flash_loans) >= 2:
        scores[AttackType.LP_DRAIN] = min(0.85, 0.3 + lp_hits * 0.15)

    if len(flash_loans) >= 3:
        scores[AttackType.CROSS_PROTOCOL_CHAIN] = min(0.7, 0.3 + len(flash_loans) * 0.1)

    if gov_hits >= 1:
        scores[AttackType.GOVERNANCE_ATTACK] = min(0.9, 0.4 + gov_hits * 0.25)

    # Reentrancy indicators
    if "call" in input_lower and "reentrancy" in input_lower:
        scores[AttackType.REENTRANCY_EXPLOIT] = 0.85

    # Default to arbitrage for single flash loan with small profit
    if not scores:
        scores[AttackType.ARBITRAGE] = 0.5

    best_type: AttackType = AttackType.ARBITRAGE
    best_score = 0.0
    for atype, ascore in scores.items():
        if ascore > best_score:
            best_type = atype
            best_score = ascore

    return best_type, best_score

def _calculate_severity(
    attacker_profit: float,
    victim_loss: float,
    sophistication: float,
) -> AttackSeverity:
    """Calculate attack severity based on financial impact and sophistication.

    Args:
        attacker_profit: USD profit made by attacker
        victim_loss: USD loss suffered by victims/protocols
        sophistication: 0-10 sophistication score

    Returns:
        Appropriate AttackSeverity level
    """
    total_impact = attacker_profit + victim_loss

    if total_impact > 1_000_000 or sophistication >= 9.0:
        return AttackSeverity.CRITICAL
    elif total_impact > 100_000 or sophistication >= 7.0:
        return AttackSeverity.HIGH
    elif total_impact > 10_000 or sophistication >= 5.0:
        return AttackSeverity.MEDIUM
    elif total_impact > 0:
        return AttackSeverity.LOW
    return AttackSeverity.INFO

def _score_sophistication(
    flash_loans: list[FlashLoanCall],
    detection_methods: list[DetectionMethod],
    attack_type: AttackType,
) -> float:
    """Score the sophistication of an attack from 0-10.

    Higher scores indicate more complex, multi-step exploits.
    """
    score = 1.0

    # Multiple flash loans = more sophisticated
    if len(flash_loans) >= 2:
        score += 2.0
    if len(flash_loans) >= 4:
        score += 2.0

    # Detection methods indicate complexity
    if DetectionMethod.LIFECYCLE_PATTERN in detection_methods:
        score += 1.0
    if DetectionMethod.ORACLE_DEVIATION in detection_methods:
        score += 1.5
    if DetectionMethod.MULTIPLE_CALLS in detection_methods:
        score += 1.0

    # Attack type modifiers
    if attack_type in (
        AttackType.CROSS_PROTOCOL_CHAIN,
        AttackType.GOVERNANCE_ATTACK,
        AttackType.REENTRANCY_EXPLOIT,
    ):
        score += 2.0
    if attack_type == AttackType.PRICE_ORACLE_MANIPULATION:
        score += 1.5

    return min(10.0, score)

def _is_known_flashloan_provider(address: str, chain: str) -> bool:
    """Check if an address is a known flash loan provider on the given chain."""
    address_lower = address.lower()
    providers = FLASHLOAN_PROVIDERS.get(chain, [])
    return any(p.lower() == address_lower for p in providers)

def _resolve_shared_aave_sig(
    provider_address: str,
    chain: str,
) -> FlashLoanProtocol | None:
    """Resolve the shared AAVE flash loan signature (0xab9c4b5d).

    Multiple protocols share this signature: Aave V2, Aave V3 Simple,
    and Silo Finance. The provider address is used for disambiguation.

    Args:
        provider_address: Contract address that received the flash loan call
        chain: Chain identifier

    Returns:
        Resolved FlashLoanProtocol, or UNKNOWN if ambiguous
    """
    _ = chain  # Unused currently, available for chain-specific resolution
    addr_lower = provider_address.lower()

    # Known Aave V2 LendingPool address (Ethereum mainnet)
    if addr_lower == "0x7d2768de32b0b80b7a3454c06bdac94a69ddc7a9":
        return FlashLoanProtocol.AAVE_V2

    # Known Aave V3 Pool addresses
    aave_v3_addresses = {
        "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",  # Ethereum
        "0x794a61358d6845594f94dc1db02a252b5b4814ad",  # Arbitrum/Polygon
        "0xa238dd80c259a72e81d7e4664a9801593f98d1c5",  # Base
        "0x9e7ab700b0c2b0a79bf265aa143b1d72b6db6f31",  # Optimism
        "0x4f6c2c9e3a7bdb5e5f5f5f5f5f5f5f5f5f5f5f5f",  # Avalanche
    }
    if addr_lower in aave_v3_addresses:
        return FlashLoanProtocol.AAVE_V3

    # Unknown provider with shared sig - return UNKNOWN
    return FlashLoanProtocol.UNKNOWN

def _estimate_token_price_usd(
    token_address: str,
    chain: str,
    amount_raw: str,
) -> float:
    """Estimate USD value of a token amount using oracle or cached price.

    In production, this reads from the RMI price service / oracle. Here we
    use a simplified estimation for standalone operation.

    TODO: Replace with DataBus / price_consensus module call for production.
    """
    # Simplified estimation - in production, call price_consensus
    # or DataBus price feed
    _ = chain
    try:
        amount_int = int(amount_raw, 16) if amount_raw.startswith("0x") else int(amount_raw)
    except (ValueError, TypeError):
        return 0.0

    # Without an oracle, we estimate based on common token decimals.
    # Production should use DataBus price feed.
    return float(amount_int) / 1e36  # Rough heuristic for common tokens
