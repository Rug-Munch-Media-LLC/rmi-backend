"""SENTINEL - Enhanced Bundle & Sniper Detection.

THIN ADAPTER. Canonical source: ``rugmunch_scanner.detectors.bundle_detector``.
The implementation was superseded by the private rugmunch-scanner repo; this
module only preserves the backward-compatible ``app.domains.scanners.
bundle_detector`` import path for callers (sentinel_pipeline, databus
token_launch_detector, rugmaps early_buyers, unit tests). All public symbols
are re-exported explicitly from the scanner package. Explorer/program
constants (JITO_PROGRAMS, BUNDLER_PROGRAMS, EVM_EXPLORER_*) live as class
attributes on ``BundleDetector`` in the canonical source.
"""

from __future__ import annotations

from rugmunch_scanner.detectors.bundle_detector import (
    BehaviorCluster,
    BundleDetector,
    BundledWallet,
    BundleReport,
    FundingStep,
    SameBlockSniper,
    _EarlyBuyer,
    logger,
)

__all__ = [
    "BehaviorCluster",
    "BundleDetector",
    "BundleReport",
    "BundledWallet",
    "FundingStep",
    "SameBlockSniper",
    "_EarlyBuyer",
    "logger",
]
