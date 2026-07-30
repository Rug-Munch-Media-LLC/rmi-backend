"""Wallet Monitor — Burst Detector."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domains.wallet.monitor.config import CONFIG, get_redis
from app.domains.wallet.monitor.models import BurstSignal


class BurstDetector:
    """Tracks tx velocity per wallet using a rolling Redis sorted set of timestamps."""

    @staticmethod
    def _velocity_key(wallet: str, chain: str) -> str:
        return f"wallet:velocity:{chain}:{wallet.lower()}"

    @classmethod
    async def record(cls, wallet: str, chain: str, tx_hash: str) -> None:
        r = await get_redis()
        if not r:
            return
        key = cls._velocity_key(wallet, chain)
        now = datetime.now(UTC).timestamp()
        await r.zadd(key, {tx_hash: now})
        cutoff = now - CONFIG["WALLET_TX_VELOCITY_WINDOW"]
        await r.zremrangebyscore(key, 0, cutoff)
        await r.expire(key, CONFIG["WALLET_TX_VELOCITY_WINDOW"] + 60)

    @classmethod
    async def check(cls, wallet: str, chain: str) -> BurstSignal | None:
        r = await get_redis()
        if not r:
            return None
        key = cls._velocity_key(wallet, chain)
        now = datetime.now(UTC).timestamp()
        cutoff = now - CONFIG["WALLET_TX_VELOCITY_WINDOW"]
        count = await r.zcount(key, cutoff, now)
        if count >= CONFIG["WALLET_BURST_THRESHOLD"]:
            sev = "critical" if count >= CONFIG["WALLET_BURST_THRESHOLD"] * 2 else "warning"
            return BurstSignal(
                wallet=wallet,
                chain=chain,
                tx_count=count,
                window_seconds=CONFIG["WALLET_TX_VELOCITY_WINDOW"],
                severity=sev,
                timestamp=datetime.now(UTC).isoformat(),
            )
        return None
