"""Stub mempool_sentinel - real-time attack detection"""

from typing import Any


class SentinelManager:
    """Manages mempool sentinels for real-time monitoring."""

    def __init__(self):
        self.monitors: dict[str, Any] = {}

    def start_monitor(self, contract: str, chain: str = "base") -> dict[str, Any]:
        """Start monitoring a contract."""
        return {"contract": contract, "chain": chain, "active": True}

    def stop_monitor(self, contract: str) -> dict[str, Any]:
        """Stop monitoring a contract."""
        return {"contract": contract, "active": False}

    def get_alerts(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent alerts."""
        return []
