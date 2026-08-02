"""
Smart Contract Honeypot & Malicious Token Detector
===================================================
Comprehensive analysis of token contracts for honeypot traps, rug-pull
patterns, sell restrictions, hidden malicious functions, and upgrade risks.

⚠️ DEPRECATED SHIM — import directly from app.domains.detectors.smart_contract_honeypot_detector instead
This shim exists for backward compatibility and will be removed in a future release.

Usage:
    from app.domains.detectors.smart_contract_honeypot_detector import HoneypotDetector
    detector = HoneypotDetector()
    result = await detector.analyze_token("0x...", chain="ethereum")
"""

from __future__ import annotations

from app.domains.detectors.smart_contract_honeypot_detector import (
    BENIGN_SELECTORS,
    EVM_CHAINS,
    HONEYPOT_THRESHOLDS,
    KNOWN_MALICIOUS_SELECTORS,
    FindingSeverity,
    HoneypotAnalysisResult,
    HoneypotDetector,
    HoneypotType,
    SecurityFinding,
    TokenInfo,
    fetch_with_fallback,
)

__all__ = [
    "BENIGN_SELECTORS",
    "EVM_CHAINS",
    "HONEYPOT_THRESHOLDS",
    "KNOWN_MALICIOUS_SELECTORS",
    "FindingSeverity",
    "HoneypotAnalysisResult",
    "HoneypotDetector",
    "HoneypotType",
    "SecurityFinding",
    "TokenInfo",
    "fetch_with_fallback",
]
