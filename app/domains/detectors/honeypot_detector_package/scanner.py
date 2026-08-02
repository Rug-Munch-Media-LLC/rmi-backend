"""
Honeypot Detector — Main detection engine
Analyzes token contracts for honeypot mechanisms, rug-pull patterns, and
malicious functionality across EVM chains.

⚠️ DEPRECATED SHIM — import directly from app.domains.detectors.smart_contract_honeypot_detector instead
This shim exists for backward compatibility and will be removed in a future release.
"""

from __future__ import annotations

from app.domains.detectors.smart_contract_honeypot_detector.detector import HoneypotDetector

__all__ = ["HoneypotDetector"]
