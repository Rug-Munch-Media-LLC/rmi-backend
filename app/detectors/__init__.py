"""
RugMunch Detectors Package
==========================
Real-time detection and analysis of blockchain attacks, scams, and
suspicious activity across all supported chains.

This package consolidates the legacy top-level detector/scanner/analyzer
modules that previously lived directly under ``app/``. The legacy import
paths (``from app.<module> import X``) are preserved by thin compatibility
shims at ``app/<module>.py`` and via the :mod:`app.compat` package.

Usage:
    from app.detectors import FlashLoanAttackDetector
    detector = FlashLoanAttackDetector()

Convenience module access:
    from app.detectors import clone_scanner, token_scanner
"""

from __future__ import annotations

from app.detectors import (
    bundle_detector,
    clone_scanner,
    degen_security_scanner,
    flash_loan_attack_detector,
    governance_attack_detector,
    oracle_manipulation_detector,
    pump_dump_manipulation_detector,
    rug_imminence_predictor,
    rug_pull_predictor,
    smart_contract_honeypot_detector,
    token_scanner,
    trufflehog_scanner,
    unified_token_scanner,
    wallet_drain_scanner,
    wash_trading_detector,
)
from app.detectors.flash_loan import FlashLoanAttackDetector

__all__ = [
    "FlashLoanAttackDetector",
    "bundle_detector",
    "clone_scanner",
    "degen_security_scanner",
    "flash_loan_attack_detector",
    "governance_attack_detector",
    "oracle_manipulation_detector",
    "pump_dump_manipulation_detector",
    "rug_imminence_predictor",
    "rug_pull_predictor",
    "smart_contract_honeypot_detector",
    "token_scanner",
    "trufflehog_scanner",
    "unified_token_scanner",
    "wallet_drain_scanner",
    "wash_trading_detector",
]
