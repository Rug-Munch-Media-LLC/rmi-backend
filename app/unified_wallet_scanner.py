"""Deprecated — use app.domains.wallet.unified_wallet_scanner instead."""

from __future__ import annotations

from app.domains.wallet.unified_wallet_scanner import (
    BACKEND,
    MODEL,
    OLLAMA_KEY,
    OLLAMA_URL,
    UnifiedWalletScanner,
    WalletScanResult,
    get_wallet_scanner,
    logger,
)

__all__ = [
    "BACKEND",
    "MODEL",
    "OLLAMA_KEY",
    "OLLAMA_URL",
    "UnifiedWalletScanner",
    "WalletScanResult",
    "get_wallet_scanner",
    "logger",
]
