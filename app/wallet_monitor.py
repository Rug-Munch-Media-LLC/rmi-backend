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

from app.domains.wallet.wallet_monitor import *  # noqa: F403
