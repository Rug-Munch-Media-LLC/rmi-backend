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

from app.domains.detectors.mev_sandwich_detector import (
    KNOWN_MEV_BOTS,
    MEVAttackType,
    MEVBotProfile,
    MEVSandwichDetector,
    MEVScanReport,
    MEVSeverity,
    MEVTransaction,
    PoolDexType,
    PoolInfo,
    SandwichAttack,
    _check_pool_vulnerability,
    _estimate_mev_vulnerability_score,
    _is_sandwich_pattern,
)

if __name__ == "__main__":
    from app.domains.detectors.mev_sandwich_detector.cli import main as _main

    _main()


__all__ = [
    "KNOWN_MEV_BOTS",
    "MEVAttackType",
    "MEVBotProfile",
    "MEVSandwichDetector",
    "MEVScanReport",
    "MEVSeverity",
    "MEVTransaction",
    "PoolDexType",
    "PoolInfo",
    "SandwichAttack",
    "_check_pool_vulnerability",
    "_estimate_mev_vulnerability_score",
    "_is_sandwich_pattern",
]
