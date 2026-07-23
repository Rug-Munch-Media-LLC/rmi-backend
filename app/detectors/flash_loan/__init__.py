"""
Flash Loan Attack Detection Package
====================================
Real-time detection and analysis of flash loan-based attacks across all
supported EVM chains.

Usage:
    from app.detectors.flash_loan import FlashLoanAttackDetector

    detector = FlashLoanAttackDetector()
    report = await detector.scan(blocks_back=50)
    for attack in report.attacks:
        logger.info(attack.summary())
"""

# Re-export main detector class
from app.detectors.flash_loan.detector import FlashLoanAttackDetector

# Re-export data models
from app.detectors.flash_loan.models import (
    AttackSeverity,
    AttackType,
    DetectionMethod,
    FlashLoanAttack,
    FlashLoanCall,
    FlashLoanProtocol,
    ScanReport,
    TransactionTrace,
)

# Re-export algorithm constants
from app.detectors.flash_loan.algorithms import (
    AAVE_V2_FLASHLOAN_SIG,
    AAVE_V3_FLASHLOAN_SIG,
    AAVE_V3_FLASHSIMPLE_SIG,
    BALANCER_FLASHLOAN_SIG,
    DYDX_FLASHLOAN_SIG,
    EULER_FLASHLOAN_SIG,
    FLASHLOAN_PROVIDERS,
    FLASHLOAN_SIGNATURES,
    MAKER_FLASHMINT_SIG,
    MORPHO_FLASHLOAN_SIG,
    RADIANT_FLASHLOAN_SIG,
    SHARED_AAVE_FLASHLOAN_SIG,
    SILO_FLASHLOAN_SIG,
    SPARK_FLASHLOAN_SIG,
    UNISWAP_V3_FLASH_SIG,
    _calculate_severity,
    _detect_attack_type,
    _estimate_token_price_usd,
    _is_known_flashloan_provider,
    _resolve_shared_aave_sig,
    _score_sophistication,
)

__all__ = [
    # Detector
    "FlashLoanAttackDetector",
    # Models
    "FlashLoanProtocol",
    "AttackType",
    "AttackSeverity",
    "DetectionMethod",
    "FlashLoanCall",
    "TransactionTrace",
    "FlashLoanAttack",
    "ScanReport",
    # Algorithm constants
    "AAVE_V2_FLASHLOAN_SIG",
    "AAVE_V3_FLASHLOAN_SIG",
    "AAVE_V3_FLASHSIMPLE_SIG",
    "BALANCER_FLASHLOAN_SIG",
    "DYDX_FLASHLOAN_SIG",
    "EULER_FLASHLOAN_SIG",
    "FLASHLOAN_PROVIDERS",
    "FLASHLOAN_SIGNATURES",
    "MAKER_FLASHMINT_SIG",
    "MORPHO_FLASHLOAN_SIG",
    "RADIANT_FLASHLOAN_SIG",
    "SHARED_AAVE_FLASHLOAN_SIG",
    "SILO_FLASHLOAN_SIG",
    "SPARK_FLASHLOAN_SIG",
    "UNISWAP_V3_FLASH_SIG",
    # Algorithm functions
    "_calculate_severity",
    "_detect_attack_type",
    "_estimate_token_price_usd",
    "_is_known_flashloan_provider",
    "_resolve_shared_aave_sig",
    "_score_sophistication",
]
