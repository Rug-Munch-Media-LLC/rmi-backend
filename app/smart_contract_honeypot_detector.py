"""
Smart Contract Honeypot Detector
================================
Detects honeypot smart contracts across EVM chains - tokens that appear
tradeable but prevent selling through hidden mechanisms.

Backward-compatibility shim: this module now lives in ``app.domains.detectors``.

Usage:
    from app.smart_contract_honeypot_detector import HoneypotDetector
"""

from __future__ import annotations
import sys as _sys
from app.domains.detectors.smart_contract_honeypot_detector import *  # noqa: F401, F403

if __name__ == "__main__":
    from app.domains.detectors.smart_contract_honeypot_detector import main as _main
    _main()
