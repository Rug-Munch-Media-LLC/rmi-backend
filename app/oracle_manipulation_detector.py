"""
Oracle Manipulation Detector
============================
Detection and analysis of price oracle manipulation attacks across DeFi
protocols, including flash loan-assisted attacks and TWAP manipulation.

Backward-compatibility shim: this module now lives in ``app.domains.detectors``.

Usage:
    from app.oracle_manipulation_detector import OracleManipulationDetector
"""

import sys as _sys
from app.domains.detectors.oracle_manipulation_detector import *  # noqa: F401, F403

if __name__ == "__main__":
    from app.domains.detectors.oracle_manipulation_detector import main as _main
    _main()
