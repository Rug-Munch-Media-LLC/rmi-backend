"""
MEV Sandwich Detector
=====================
Detects sandwich attacks on DEX trades by analyzing mempool transactions
and on-chain execution patterns.

Backward-compatibility shim: this module now lives in ``app.domains.detectors``.

Usage:
    from app.mev_sandwich_detector import MEVSandwichDetector
"""

from __future__ import annotations

from app.domains.detectors.mev_sandwich_detector import *  # noqa: F403

if __name__ == "__main__":
    from app.domains.detectors.mev_sandwich_detector import main as _main
    _main()
