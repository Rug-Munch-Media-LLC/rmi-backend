"""Wallet Monitor — Watchlist Manager."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domains.wallet.monitor.config import CONFIG, get_redis


class WatchlistManager:
    """Subscribe / unsubscribe wallet addresses across chains. Persisted in Redis."""

    @staticmethod
    async def add(wallet: str, chain: str = "ethereum", tags: list[str] | None = None) -> bool:
        r = await get_redis()
        if not r:
            return False
        key = f"{CONFIG['WATCHLIST_KEY']}:{chain}"
        await r.zadd(key, {wallet.lower(): datetime.now(UTC).timestamp()})
        if tags:
            await r.sadd(f"{CONFIG['WATCHLIST_KEY']}:{chain}:tags:{wallet.lower()}", *tags)
        return True

    @staticmethod
    async def remove(wallet: str, chain: str = "ethereum") -> bool:
        r = await get_redis()
        if not r:
            return False
        key = f"{CONFIG['WATCHLIST_KEY']}:{chain}"
        await r.zrem(key, wallet.lower())
        await r.delete(f"{CONFIG['WATCHLIST_KEY']}:{chain}:tags:{wallet.lower()}")
        return True

    @staticmethod
    async def list(chain: str = "ethereum", limit: int = 1000) -> list[dict]:
        r = await get_redis()
        if not r:
            return []
        key = f"{CONFIG['WATCHLIST_KEY']}:{chain}"
        wallets = await r.zrevrange(key, 0, limit - 1, withscores=True)
        result = []
        for w, score in wallets:
            tk = f"{CONFIG['WATCHLIST_KEY']}:{chain}:tags:{w}"
            tags = await r.smembers(tk) if r else set()
            result.append(
                {
                    "wallet": w,
                    "chain": chain,
                    "subscribed_at": datetime.fromtimestamp(score, tz=UTC).isoformat(),
                    "tags": list(tags),
                }
            )
        return result

    @staticmethod
    async def is_watched(wallet: str, chain: str = "ethereum") -> bool:
        r = await get_redis()
        if not r:
            return False
        s = await r.zscore(f"{CONFIG['WATCHLIST_KEY']}:{chain}", wallet.lower())
        return s is not None

    @staticmethod
    async def all_watched_set(chain: str = "ethereum") -> set[str]:
        r = await get_redis()
        if not r:
            return set()
        wallets = await r.zrange(f"{CONFIG['WATCHLIST_KEY']}:{chain}", 0, -1)
        return {w.lower() for w in wallets}
