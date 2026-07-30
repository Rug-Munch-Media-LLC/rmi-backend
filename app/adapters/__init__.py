"""
Adapters
========
Adapters for external blockchain and financial data services.

Submodules:
    binance_web3  — Binance exchange integration
"""

from __future__ import annotations

from app.adapters.binance_web3 import CHAIN_IDS, CHAIN_NAMES, get_wallet_holdings

__all__ = ["CHAIN_IDS", "CHAIN_NAMES", "get_wallet_holdings"]
