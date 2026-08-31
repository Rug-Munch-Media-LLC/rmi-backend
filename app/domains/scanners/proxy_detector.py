"""SENTINEL - Proxy Detection + Implementation Analysis.

THIN ADAPTER. Canonical source: ``rugmunch_scanner.detectors.proxy_detector``.
The implementation was superseded by the private rugmunch-scanner repo; this
module only preserves the backward-compatible ``app.domains.scanners.
proxy_detector`` import path for callers (sentinel_pipeline, x402 report tools).
All public symbols are re-exported explicitly from the scanner package.
"""

from __future__ import annotations

from rugmunch_scanner.detectors.proxy_detector import (
    EIP1967_ADMIN_SLOT,
    EIP1967_BEACON_SLOT,
    EIP1967_IMPLEMENTATION_SLOT,
    KNOWN_RUG_PATTERNS,
    OLD_PROXY_IMPLEMENTATION_SLOT,
    OLD_PROXY_SLOTS,
    OWNER_SELECTOR,
    PENDING_OWNER_SELECTOR,
    TIMELOCK_DELAY_SELECTOR,
    TIMELOCK_GRACE_SELECTOR,
    ProxyDetector,
    ProxyReport,
    logger,
)

__all__ = [
    "EIP1967_ADMIN_SLOT",
    "EIP1967_BEACON_SLOT",
    "EIP1967_IMPLEMENTATION_SLOT",
    "KNOWN_RUG_PATTERNS",
    "OLD_PROXY_IMPLEMENTATION_SLOT",
    "OLD_PROXY_SLOTS",
    "OWNER_SELECTOR",
    "PENDING_OWNER_SELECTOR",
    "TIMELOCK_DELAY_SELECTOR",
    "TIMELOCK_GRACE_SELECTOR",
    "ProxyDetector",
    "ProxyReport",
    "logger",
]
