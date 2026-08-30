"""
:mod:`app.compat` — RugMunch legacy detector/scanner compatibility layer.
===========================================================================
Phase-1 of the rmi-backend de-monolithization moved the legacy top-level
detector/scanner/analyzer modules into :mod:`app.detectors`. To keep every
legacy import path alive, the original ``from app.<module> import X`` paths are
preserved by thin shims at ``app/<module>.py``; this package additionally
re-exports the moved modules under one stable roof.

Legacy import surface that this package backs:

    from app.compat import clone_scanner        # module
    from app.compat import FlashLoanAttackDetector

The canonical location for NEW code is :mod:`app.detectors`.
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
