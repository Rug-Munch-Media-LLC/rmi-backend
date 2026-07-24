"""Wallet Monitor — Manager (multi-chain orchestrator)."""
from __future__ import annotations

import asyncio
from typing import Any

from app.domains.wallet.monitor.channel import AlertChannel
from app.domains.wallet.monitor.monitor import WalletMonitor
from app.domains.wallet.monitor.watchlist import WatchlistManager


class WalletMonitorManager:
    """Manages WalletMonitor instances across multiple chains."""

    def __init__(self) -> None:
        self.monitors: dict[str, WalletMonitor] = {}
        self._webhooks: list[str] = []

    def register_webhook(self, url: str) -> None:
        self._webhooks.append(url)

    async def start_chain(self, chain: str) -> None:
        if chain in self.monitors: return
        monitor = WalletMonitor(chain)
        monitor.on_alert(self._alert_handler)
        self.monitors[chain] = monitor
        asyncio.create_task(monitor.start())

    async def stop_chain(self, chain: str) -> None:
        mon = self.monitors.pop(chain, None)
        if mon: await mon.stop()

    async def stop_all(self) -> None:
        for chain in list(self.monitors.keys()):
            await self.stop_chain(chain)

    async def _alert_handler(self, alert: dict) -> None:
        if self._webhooks:
            await AlertChannel.dispatch_webhooks(alert, self._webhooks)

    async def add_watch(self, wallet: str, chain: str = "ethereum", tags: list[str] | None = None) -> dict:
        ok = await WatchlistManager.add(wallet, chain, tags)
        mon = self.monitors.get(chain)
        if mon: await mon._refresh_watchlist()
        return {"success": ok, "wallet": wallet, "chain": chain}

    async def remove_watch(self, wallet: str, chain: str = "ethereum") -> dict:
        ok = await WatchlistManager.remove(wallet, chain)
        mon = self.monitors.get(chain)
        if mon: await mon._refresh_watchlist()
        return {"success": ok, "wallet": wallet, "chain": chain}

    async def list_watches(self, chain: str = "ethereum", limit: int = 1000) -> list[dict]:
        return await WatchlistManager.list(chain, limit)

    def get_stats(self) -> dict[str, Any]:
        return {chain: mon.get_stats() for chain, mon in self.monitors.items()}
