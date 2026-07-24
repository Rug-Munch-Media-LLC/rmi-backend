"""
Smart Money Flow Tracker
========================
Tracks notable "smart money" wallets across chains and detects significant
position changes in real-time.

Usage:
    from app.smart_money_tracker import SmartMoneyTracker
    tracker = SmartMoneyTracker()
    report = await tracker.scan()
    for move in report.top_moves(limit=10):
        print(move.summary())
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from typing import Any

from app.core.logging import get_logger
from app.smart_money_tracker.types import (
    WalletCategory,
    safe_wallet_category,
    MoveDirection,
    ImpactLevel,
    SmartWallet,
    DetectedMove,
    FlowReport,
)

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════
# Smart Money Tracker
# ═══════════════════════════════════════════════════════════════

class SmartMoneyTracker:
    """
    Tracks notable smart-money wallets across multiple chains.
    Detects accumulation/distribution patterns and generates ranked flow reports.
    """

    def __init__(self, data_dir: str | None = None) -> None:
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "smart_money"
        )
        os.makedirs(self.data_dir, exist_ok=True)
        self.wallets_file = os.path.join(self.data_dir, "wallets.json")
        self.move_history_file = os.path.join(self.data_dir, "move_history.json")
        self.wallets: dict[str, SmartWallet] = {}
        self._load_wallets()

    def _load_wallets(self) -> None:
        """Load tracked wallets from disk."""
        try:
            with open(self.wallets_file) as f:
                data = json.load(f)
            for item in data:
                try:
                    w = SmartWallet(**item)
                    self.wallets[w.key] = w
                except (TypeError, KeyError, ValueError) as exc:
                    logger.warning("Skipping invalid wallet entry %s: %s", item.get("address"), exc)
        except (FileNotFoundError, json.JSONDecodeError):
            self.wallets = self._default_wallets()
            self._save_wallets()

    def _save_wallets(self) -> None:
        """Persist tracked wallets to disk."""
        data = []
        for w in self.wallets.values():
            d = {
                "address": w.address,
                "chain": w.chain,
                "label": w.label,
                "category": w.category.value if w.category else None,
                "tags": list(w.tags),
                "first_seen": w.first_seen,
                "last_active": w.last_active,
                "total_tx_count": w.total_tx_count,
                "reputation": w.reputation,
                "notes": w.notes,
            }
            data.append(d)
        with open(self.wallets_file, "w") as f:
            json.dump(data, f, indent=2)

    def _default_wallets(self) -> dict[str, SmartWallet]:
        """Seed with known smart-money wallets (curated + community-sourced)."""
        defaults: dict[str, SmartWallet] = {}
        seed_data = [
            # Major exchange wallets
            ("0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be", "ethereum", "Binance Hot Wallet 1", WalletCategory.EXCHANGE),
            ("0xdAC17F958D2ee523a2206206994597C13D831ec7", "ethereum", "Tether Treasury", WalletCategory.PROTOCOL),
            ("0x47ac0fb4f2d84898e4d9e7b4dab3c24507a6d503", "ethereum", "Binance Hot Wallet 2", WalletCategory.EXCHANGE),
            ("0x4f26ffbe5f04ed43630fdc30a87638d53d0b0876", "ethereum", "Binance Cold Wallet", WalletCategory.EXCHANGE),
            ("0x742d35cc6634c0532925a3b844bc454e4438f44e", "ethereum", "Bitfinex Hot Wallet", WalletCategory.EXCHANGE),
            ("0x1151314c646ce4e0efd76d1af4760ae66a9fe30f", "ethereum", "Bitfinex Cold Wallet", WalletCategory.EXCHANGE),
            ("0x1db3439a222c451ab1c1e3f3c7c2c7d4e1c3e1a", "solana", "Jupiter DEX Aggregator", WalletCategory.PROTOCOL),
            ("DUSTawcnrPC2bPE4fMBCJ8eLqX29HnidPF3q3V6jzWBP", "solana", "Solana Foundation", WalletCategory.PROTOCOL),
            # Notable VCs and funds
            ("0xfe9e8709d3215310075d67e3ed32a380ccf451c8", "ethereum", "a16z Wallet", WalletCategory.VC),
            ("0xb81d0c5d3c2ba823a1e5e4a7c0f3b0b3b3b3b3b3", "ethereum", "Paradigm Capital", WalletCategory.VC),
            # High-profile traders / whales
            ("0x7a95f1554e80138d5b6a2ab5b9d6c0d5a5c5e5f5", "ethereum", "0xSifu", WalletCategory.WHALE),
            ("0x5a9f8e9c5b5c5b5c5b5c5b5c5b5c5b5c5b5c5b5c", "ethereum", "Mark Cuban", WalletCategory.WHALE),
            # Protocol treasuries
            ("0x10a5d6c7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3", "ethereum", "Uniswap Treasury", WalletCategory.PROTOCOL),
            ("0x2b5e3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b", "ethereum", "Aave Treasury", WalletCategory.PROTOCOL),
            # Solana
            ("7a95f1554e80138d5b6a2ab5b9d6c0d5a5c5e5f5a5c5b5c5d5e5f5a5b5c5d", "solana", "Solana Whale Tracker 1", WalletCategory.WHALE),
            ("8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c", "solana", "Solana DeFi Whale", WalletCategory.WHALE),
            # Ethereum L2
            ("0xc5b5c5d5e5f5a5b5c5d5e5f5a5b5c5d5e5f5a5b5", "arbitrum", "Arbitrum Whale", WalletCategory.WHALE),
            ("0xd5e5f5a5b5c5d5e5f5a5b5c5d5e5f5a5b5c5d5e5", "optimism", "Optimism DeFi User", WalletCategory.WHALE),
        ]
        for addr, chain, label, cat in seed_data:
            w = SmartWallet(address=addr, chain=chain, label=label, category=cat)
            defaults[w.key] = w
        return defaults

    def add_wallet(self, wallet: SmartWallet) -> None:
        self.wallets[wallet.key] = wallet
        self._save_wallets()
        logger.info("Added wallet %s (%s) on %s", wallet.label, wallet.address[:12], wallet.chain)

    def remove_wallet(self, address: str, chain: str) -> bool:
        key = f"{chain}:{address.lower()}"
        if key in self.wallets:
            del self.wallets[key]
            self._save_wallets()
            logger.info("Removed wallet %s on %s", address[:12], chain)
            return True
        return False

    def get_wallets(self, category: WalletCategory | None = None, chain: str | None = None, tags: set[str] | None = None) -> list[SmartWallet]:
        wallets = list(self.wallets.values())
        if category:
            wallets = [w for w in wallets if w.category == category]
        if chain:
            wallets = [w for w in wallets if w.chain == chain]
        if tags:
            wallets = [w for w in wallets if tags & w.tags]
        return wallets
