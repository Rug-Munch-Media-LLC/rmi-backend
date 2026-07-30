"""
RugMunch Detectors Package
==========================
Real-time detection and analysis of blockchain attacks, scams, and
suspicious activity across all supported chains.

Usage:
    from app.detectors import FlashLoanAttackDetector
    detector = FlashLoanAttackDetector()
"""

from __future__ import annotations

from app.detectors.flash_loan import FlashLoanAttackDetector

__all__ = [
    "FlashLoanAttackDetector",
]
