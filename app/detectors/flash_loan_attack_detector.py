"""
Flash Loan Attack Detector
==========================
Real-time detection and analysis of flash loan-based attacks across all
supported EVM chains. Flash loans power >80% of major DeFi exploits -
this module catches them by tracing the borrow → manipulate → arbitrage/profit
→ repay lifecycle.

Backward-compatibility shim: this module now re-exports from the
``app.detectors.flash_loan`` package.

Usage:
    from app.flash_loan_attack_detector import FlashLoanAttackDetector

    detector = FlashLoanAttackDetector()
    report = await detector.scan(blocks_back=50)
    for attack in report.attacks:
        logger.info(attack.summary())

CLI:
    python3 -m app.detectors.flash_loan.alerts          # Full scan
    python3 -m app.detectors.flash_loan.alerts --tx 0xabc...
"""

# Re-export everything from the new package structure
from __future__ import annotations

from app.detectors.flash_loan import (
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
    AttackSeverity,
    AttackType,
    DetectionMethod,
    FlashLoanAttack,
    FlashLoanAttackDetector,
    FlashLoanCall,
    FlashLoanProtocol,
    ScanReport,
    TransactionTrace,
    _calculate_severity,
    _detect_attack_type,
    _estimate_token_price_usd,
    _is_known_flashloan_provider,
    _resolve_shared_aave_sig,
    _score_sophistication,
)

# Also re-export the CLI main() at module level for backward compat
# (the original module had `main()` and `if __name__ == "__main__"`)
from app.detectors.flash_loan.alerts import main as main

if __name__ == "__main__":
    # Run the CLI entry point when invoked directly
    main()

__all__ = [
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
    "AttackSeverity",
    "AttackType",
    "DetectionMethod",
    "FlashLoanAttack",
    "FlashLoanAttackDetector",
    "FlashLoanCall",
    "FlashLoanProtocol",
    "ScanReport",
    "TransactionTrace",
    "_calculate_severity",
    "_detect_attack_type",
    "_estimate_token_price_usd",
    "_is_known_flashloan_provider",
    "_resolve_shared_aave_sig",
    "_score_sophistication",
    "main",
]
