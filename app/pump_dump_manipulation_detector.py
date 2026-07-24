"""
Pump & Dump Manipulation Detector
==================================
Detects pump-and-dump schemes by analyzing trading patterns, social signals,
and wallet coordination across all supported chains.

Backward-compatibility shim: this module now lives in ``app.domains.detectors``.

Usage:
    from app.pump_dump_manipulation_detector import PumpDumpDetector

CLI:
    python3 -m app.pump_dump_manipulation_detector 0x... --chain ethereum
    python3 -m app.pump_dump_manipulation_detector 0x... --chain solana --alert
"""

from __future__ import annotations

from app.domains.detectors.pump_dump_manipulation_detector import *

if __name__ == "__main__":
    from app.domains.detectors.pump_dump_manipulation_detector import main as _main
    _main()
