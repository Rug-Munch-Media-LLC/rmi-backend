"""SENTINEL - Oracle Manipulation Detector.

THIN ADAPTER. Canonical source: ``rugmunch_scanner.detectors.oracle_manipulation``.
The implementation was superseded by the private rugmunch-scanner repo; this
module only preserves the backward-compatible ``app.domains.scanners.
oracle_manipulation`` import path for callers (sentinel_pipeline, x402 report
tools). All public symbols are re-exported explicitly from the scanner package.
"""

from __future__ import annotations

from rugmunch_scanner.detectors.oracle_manipulation import (
    CHAINLINK_FEED_REGISTRY,
    EIP1967_IMPLEMENTATION_SLOT,
    ORACLE_ABI_SELECTORS,
    OracleInfo,
    OracleManipulationDetector,
    OracleManipulationReport,
    logger,
)

__all__ = [
    "CHAINLINK_FEED_REGISTRY",
    "EIP1967_IMPLEMENTATION_SLOT",
    "ORACLE_ABI_SELECTORS",
    "OracleInfo",
    "OracleManipulationDetector",
    "OracleManipulationReport",
    "logger",
]
