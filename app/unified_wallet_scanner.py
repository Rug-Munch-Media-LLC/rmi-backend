"""Deprecated — use app.domains.wallet.unified_wallet_scanner instead."""

from __future__ import annotations

from app.domains.wallet.unified_wallet_scanner import (
    logger,
    BACKEND,
    OLLAMA_URL,
    OLLAMA_KEY,
    MODEL,
    WalletScanResult,
    UnifiedWalletScanner,
    get_wallet_scanner,
)


__all__ = [
    "logger",
    "BACKEND",
    "OLLAMA_URL",
    "OLLAMA_KEY",
    "MODEL",
    "WalletScanResult",
    "UnifiedWalletScanner",
    "get_wallet_scanner",
]
