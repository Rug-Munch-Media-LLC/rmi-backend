"""SENTINEL - Honeypot Detector.

THIN ADAPTER. Canonical source: ``rugmunch_scanner.detectors.honeypot_detector``.
The implementation was superseded by the private rugmunch-scanner repo; this
module only preserves the backward-compatible ``app.domains.scanners.
honeypot_detector`` import path for callers (sentinel_pipeline, databus
token_launch_detector, rug_imminence_predictor). All public symbols are
re-exported explicitly from the scanner package.
"""

from __future__ import annotations

from rugmunch_scanner.detectors.honeypot_detector import (
    CHAIN_ALIASES,
    DEXSCREENER_URL,
    ERC20_TRANSFER_SEL,
    EXTREME_TAX_PCT,
    HIGH_TAX_PCT,
    JUPITER_QUOTE_URL,
    LAMPORTS_PER_SOL,
    SELL_FAIL_CONF,
    SOL_MINT,
    TOKEN_DECIMALS_SOL,
    VARIABLE_TAX_CONF,
    HoneypotDetector,
    HoneypotReport,
    SellSimulationResult,
    logger,
)

__all__ = [
    "CHAIN_ALIASES",
    "DEXSCREENER_URL",
    "ERC20_TRANSFER_SEL",
    "EXTREME_TAX_PCT",
    "HIGH_TAX_PCT",
    "JUPITER_QUOTE_URL",
    "LAMPORTS_PER_SOL",
    "SELL_FAIL_CONF",
    "SOL_MINT",
    "TOKEN_DECIMALS_SOL",
    "VARIABLE_TAX_CONF",
    "HoneypotDetector",
    "HoneypotReport",
    "SellSimulationResult",
    "logger",
]
