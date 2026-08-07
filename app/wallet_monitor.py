"""
Wallet Monitor
==============
Monitors wallet activity across all supported chains, tracking transactions,
balance changes, and behavioral patterns.

Backward-compatibility shim: this module now lives in ``app.domains.wallet``.

Usage:
    from app.wallet_monitor import WalletMonitor
"""

from __future__ import annotations

from app.domains.wallet.wallet_monitor import (
    AlertChannel,
    AlertEngine,
    AutoLabeler,
    BurstDetector,
    BurstSignal,
    LabelSuggestion,
    WalletAlert,
    WalletMonitor,
    WalletMonitorManager,
    WalletTx,
    WatchlistManager,
)

__all__ = [
    "AlertChannel",
    "AlertEngine",
    "AutoLabeler",
    "BurstDetector",
    "BurstSignal",
    "LabelSuggestion",
    "WalletAlert",
    "WalletMonitor",
    "WalletMonitorManager",
    "WalletTx",
    "WatchlistManager",
]
