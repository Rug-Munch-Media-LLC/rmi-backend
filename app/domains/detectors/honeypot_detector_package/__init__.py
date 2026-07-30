"""
Smart Contract Honeypot & Malicious Token Detector
===================================================
Comprehensive analysis of token contracts for honeypot traps, rug-pull
patterns, sell restrictions, hidden malicious functions, and upgrade risks.

Usage:
    from app.domains.detectors.smart_contract_honeypot_detector import HoneypotDetector
    detector = HoneypotDetector()
    result = await detector.analyze_token("0x...", chain="ethereum")
"""

from __future__ import annotations

from app.domains.detectors.honeypot_detector_package.models import (
    FindingSeverity,
    HoneypotAnalysisResult,
    HoneypotType,
    SecurityFinding,
    TokenInfo,
)
from app.domains.detectors.honeypot_detector_package.scanner import HoneypotDetector

__all__ = [
    "FindingSeverity",
    "HoneypotAnalysisResult",
    "HoneypotDetector",
    "HoneypotType",
    "SecurityFinding",
    "TokenInfo",
]
