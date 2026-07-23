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
from app.detectors.flash_loan import *  # noqa: F401, F403, E402

# Also re-export the CLI main() at module level for backward compat
# (the original module had `main()` and `if __name__ == "__main__"`)
from app.detectors.flash_loan.alerts import main as main  # noqa: F401, E402

import sys as _sys
import runpy as _runpy

if __name__ == "__main__":
    # Run the CLI entry point when invoked directly
    main()
