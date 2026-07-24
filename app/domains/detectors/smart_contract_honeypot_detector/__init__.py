"""
Smart Contract Honeypot & Malicious Token Detector Package.
"""
from app.domains.detectors.smart_contract_honeypot_detector.constants import (
    BENIGN_SELECTORS,
    EVM_CHAINS,
    HONEYPOT_THRESHOLDS,
    KNOWN_MALICIOUS_SELECTORS,
)
from app.domains.detectors.smart_contract_honeypot_detector.detector import (
    HoneypotDetector,
)
from app.domains.detectors.smart_contract_honeypot_detector.models import (
    FindingSeverity,
    HoneypotAnalysisResult,
    HoneypotType,
    SecurityFinding,
    TokenInfo,
)
from app.domains.detectors.smart_contract_honeypot_detector.utils import (
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
