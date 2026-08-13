"""
Smart Contract Honeypot Detector
================================
Detects honeypot smart contracts across EVM chains - tokens that appear
tradeable but prevent selling through hidden mechanisms.

DEPRECATED: backward-compatibility shim — canonical implementation lives in
``app.domains.detectors.smart_contract_honeypot_detector`` (package). Import
from there instead:

    from app.domains.detectors.smart_contract_honeypot_detector import HoneypotDetector

This shim is kept only for legacy imports (e.g. tests) and will be removed.

Usage (legacy):
    from app.smart_contract_honeypot_detector import HoneypotDetector
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

if __name__ == "__main__":
    from app.domains.detectors.smart_contract_honeypot_detector import main as _main

    _main()


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
