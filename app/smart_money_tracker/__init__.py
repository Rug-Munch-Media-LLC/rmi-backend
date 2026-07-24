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
import logging

from app.smart_money_tracker.tracker import SmartMoneyTracker
from app.smart_money_tracker.types import ImpactLevel, WalletCategory


async def run_smart_money_scan(lookback_minutes: int = 60, chains: list[str] | None = None) -> str:
    """Run a smart money scan and return a formatted report string."""
    tracker = SmartMoneyTracker()
    report = await tracker.scan(lookback_minutes=lookback_minutes, chains=chains, max_wallets=30)
    return report.summary()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def main() -> None:
        tracker = SmartMoneyTracker()
        print(f"Tracked wallets: {tracker.get_wallet_count()}")
        for cat in WalletCategory:
            count = len(tracker.get_wallets(category=cat))
            if count > 0:
                print(f"  {cat.value}: {count}")
        print((await tracker.scan(lookback_minutes=1440, max_wallets=5)).summary())

    asyncio.run(main())
