"""
RugCharts OHLCV Aggregation Engine
===================================
Real-time candle building from trade events.
Produces OHLCV bars at 1m, 5m, 15m, 1h, 4h, 1d timeframes.

Stored in Redis sorted sets for O(log N) range queries.
Wired into DataBus as 'ohlcv' chain.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import redis

from app.core.logging import get_logger

logger = get_logger("ohlcv_engine")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

TIMEFRAMES = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

CACHE_TTL = {
    "1m": 300,  # 5 min
    "5m": 900,  # 15 min
    "15m": 1800,  # 30 min
    "1h": 3600,  # 1 hour
    "4h": 14400,  # 4 hours
    "1d": 86400,  # 24 hours
}

MAX_CANDLES = 500  # Max candles returned per query


def _redis() -> Any:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=2,
    )


def _frame_key(token: str, chain: str, timeframe: str) -> str:
    """Redis sorted set key for OHLCV candles."""
    return f"ohlcv:{chain}:{token}:{timeframe}"


def _candle_cache_key(token: str, chain: str, timeframe: str, limit: int, end_ts: int | None = None) -> str:
    """Cache key for bulk candle queries."""
    end = end_ts or int(time.time())
    return f"ohlcv_cache:{chain}:{token}:{timeframe}:{limit}:{end}"


class Candle:
    """Single OHLCV candle."""

    __slots__ = ("close", "high", "low", "open", "timestamp", "trades", "volume")

    def __init__(
        self,
        ts: int,
        open_p: float = 0,
        high: float = 0,
        low: float = float("inf"),
        close: float = 0,
        volume: float = 0,
        trades: int = 0,
    ) -> None:
        self.timestamp = ts
        self.open = open_p
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.trades = trades

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "datetime": datetime.fromtimestamp(self.timestamp, tz=UTC).isoformat(),
            "open": round(self.open, 8),
            "high": round(self.high, 8),
            "low": round(self.low, 8),
            "close": round(self.close, 8),
            "volume": round(self.volume, 2),
            "trades": self.trades,
        }

    def to_array(self) -> list[Any]:
        """Compact array format: [ts, o, h, l, c, v, n]"""
        return [
            self.timestamp,
            round(self.open, 8),
            round(self.high, 8),
            round(self.low, 8),
            round(self.close, 8),
            round(self.volume, 2),
            self.trades,
        ]


class OHLCVEngine:
    """Real-time OHLCV candle builder and query engine."""

    def __init__(self) -> None:
        self._active_candles: dict[str, Candle] = {}  # key → current open candle

    def _active_key(self, token: str, chain: str, timeframe: str, bucket_ts: int) -> str:
        return f"{chain}:{token}:{timeframe}:{bucket_ts}"

    def ingest_trade(
        self,
        token: str,
        chain: str,
        price: float,
        volume: float,
        timestamp: int | None = None,
        commit: bool = True,
    ) -> dict[str, Candle]:
        """Ingest a single trade, updating all timeframe candles.

        Returns dict of timeframe → updated Candle.
        """
        ts = timestamp or int(time.time())
        updated = {}

        for tf_name, tf_seconds in TIMEFRAMES.items():
            bucket_ts = (ts // tf_seconds) * tf_seconds
            active_key = self._active_key(token, chain, tf_name, bucket_ts)

            candle = self._active_candles.get(active_key)
            if candle is None or candle.timestamp != bucket_ts:
                # Close old candle and start new
                if candle and commit:
                    self._persist_candle(token, chain, tf_name, candle)
                candle = Candle(
                    ts=bucket_ts,
                    open_p=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=volume,
                    trades=1,
                )
                self._active_candles[active_key] = candle
            else:
                # Update open candle
                if candle.open == 0:
                    candle.open = price
                candle.high = max(candle.high, price)
                candle.low = min(candle.low, price)
                candle.close = price
                candle.volume += volume
                candle.trades += 1

            updated[tf_name] = candle

        return updated

    def _persist_candle(self, token: str, chain: str, timeframe: str, candle: Candle) -> None:
        """Write candle to Redis sorted set."""
        try:
            r = _redis()
            key = _frame_key(token, chain, timeframe)
            # Store as JSON in sorted set (score = timestamp)
            r.zadd(key, {json.dumps(candle.to_dict()): candle.timestamp})
            # Trim to MAX_CANDLES
            r.zremrangebyrank(key, 0, -(MAX_CANDLES + 1))
            r.expire(key, CACHE_TTL.get(timeframe, 3600))
            r.close()
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"Failed to persist candle: {e}")

    def flush_all(self) -> None:
        """Persist all active candles to Redis."""
        for active_key, candle in list(self._active_candles.items()):
            parts = active_key.split(":")
            if len(parts) >= 4:
                chain, token, tf_name = parts[0], parts[1], parts[2]
                self._persist_candle(token, chain, tf_name, candle)
        self._active_candles.clear()

    def get_candles(
        self,
        token: str,
        chain: str,
        timeframe: str = "1h",
        limit: int = 100,
        end_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve OHLCV candles for a token.

        Args:
            token: Token address
            chain: Blockchain ID
            timeframe: '1m', '5m', '15m', '1h', '4h', '1d'
            limit: Max candles (default 100, max 500)
            end_ts: End timestamp (default: now). Returns candles up to this time.
        """
        if timeframe not in TIMEFRAMES:
            timeframe = "1h"

        limit = min(limit, MAX_CANDLES)
        end = end_ts or int(time.time())

        # Check cache first
        cache_key = _candle_cache_key(token, chain, timeframe, limit, end)
        try:
            r = _redis()
            cached = r.get(cache_key)
            if cached:
                r.close()
                return json.loads(cached)
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            pass

        try:
            r = _redis() if "r" not in dir() or not r else r
            key = _frame_key(token, chain, timeframe)
            # Get candles up to end_ts
            raw = r.zrangebyscore(key, 0, end, start=0, num=limit)

            candles = []
            for item in raw:
                try:
                    c = json.loads(item)
                    candles.append(c)
                except (httpx.HTTPError, json.JSONDecodeError):
                    pass

            # Sort by timestamp ascending
            candles.sort(key=lambda x: x["timestamp"])

            # Include active candle if within range
            tf_seconds = TIMEFRAMES[timeframe]
            bucket_ts = (end // tf_seconds) * tf_seconds
            active_key = self._active_key(token, chain, timeframe, bucket_ts)
            active = self._active_candles.get(active_key)
            # Avoid duplicating if already persisted
            if active and active.timestamp <= end and (not candles or candles[-1]["timestamp"] != active.timestamp):
                candles.append(active.to_dict())

            # Cache the result
            r.setex(cache_key, 60, json.dumps(candles[-limit:] if len(candles) > limit else candles))
            r.close()

            return candles[-limit:] if len(candles) > limit else candles

        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"OHLCV query failed: {e}")
            return []

    def get_latest_candle(self, token: str, chain: str, timeframe: str = "1h") -> dict[str, Any] | None:
        """Get the most recent candle (may be still-open)."""
        candles = self.get_candles(token, chain, timeframe, limit=1)
        return candles[0] if candles else None

    def get_price_change(self, token: str, chain: str, periods: int = 24, timeframe: str = "1h") -> dict[str, Any]:
        """Calculate price change over N periods."""
        candles = self.get_candles(token, chain, timeframe, limit=periods + 1)
        if len(candles) < 2:
            return {"change_pct": 0, "candles": 0}

        first = candles[0]["close"]
        last = candles[-1]["close"]
        change_pct = ((last - first) / first * 100) if first > 0 else 0

        return {
            "change_pct": round(change_pct, 2),
            "open": first,
            "close": last,
            "high": max(c["high"] for c in candles),
            "low": min(c["low"] for c in candles),
            "volume": sum(c["volume"] for c in candles),
            "candles": len(candles),
        }

    def stats(self) -> dict[str, Any]:
        """Engine statistics."""
        return {
            "active_candles": len(self._active_candles),
            "timeframes": list(TIMEFRAMES.keys()),
            "max_candles_per_query": MAX_CANDLES,
        }


# ── Singleton ──────────────────────────────────────────────────────

ohlcv_engine = OHLCVEngine()

# ── DataBus Provider ────────────────────────────────────────────────


async def fetch_ohlcv(
    token: str = "", chain: str = "ethereum", timeframe: str = "1h", limit: int = 100, **kw
) -> dict[str, Any] | None:
    """DataBus provider for OHLCV candle data.

    Args:
        token: Token address (use mint= or address= as aliases)
        chain: Blockchain (ethereum, solana, bsc, base, etc.)
        timeframe: 1m, 5m, 15m, 1h, 4h, 1d
        limit: Number of candles (default 100, max 500)
    """
    address = token or kw.get("mint", "") or kw.get("address", "")
    if not address:
        return None

    candles = ohlcv_engine.get_candles(address, chain, timeframe, limit)

    # Calculate summary stats
    summary = {}
    if candles:
        prices = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]
        summary = {
            "current_price": prices[-1],
            "price_change_pct": round(((prices[-1] - prices[0]) / prices[0] * 100), 2) if prices[0] > 0 else 0,
            "high_24h": max(c["high"] for c in candles),
            "low_24h": min(c["low"] for c in candles),
            "volume_24h": sum(volumes),
            "total_trades": sum(c["trades"] for c in candles),
        }

    # Also compute authenticity if we have volume data
    authenticity = None
    try:
        from app.databus.volume_authenticity import quick_authenticity_score

        auth = quick_authenticity_score(
            volume_24h=summary.get("volume_24h", 0),
            liquidity=float(kw.get("liquidity_usd", 0)),
            unique_wallets=int(kw.get("unique_wallets", 0)),
            tx_count=summary.get("total_trades", 0),
        )
        authenticity = {
            "fake_volume_pct": auth.get("fake_volume_pct", 0),
            "authentic_score": auth.get("authentic_score", 100),
            "risk_level": auth.get("risk_level", "UNKNOWN"),
        }
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    return {
        "candles": candles,
        "summary": summary,
        "authenticity": authenticity,
        "timeframe": timeframe,
        "token": address,
        "chain": chain,
        "source": "ohlcv_engine",
    }


async def ingest_trade_data(
    token: str = "",
    chain: str = "ethereum",
    price: float = 0,
    volume: float = 0,
    timestamp: int | None = None,
    **kw,
) -> dict[str, Any] | None:
    """DataBus provider: Ingest a trade and update all OHLCV candles."""
    address = token or kw.get("address", "") or kw.get("token", "")
    if not address or price <= 0:
        return None

    price = float(price)
    volume = float(volume)

    updated = ohlcv_engine.ingest_trade(address, chain, price, volume, timestamp)

    return {
        "status": "ingested",
        "token": address,
        "chain": chain,
        "price": price,
        "volume": volume,
        "updated_timeframes": list(updated.keys()),
        "source": "ohlcv_engine",
    }
