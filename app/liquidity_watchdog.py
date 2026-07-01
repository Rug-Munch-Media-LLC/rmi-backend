"""
Liquidity Expiration Watchdog
==============================
Monitors tracked tokens for approaching liquidity lock expiries.
Maintains a Redis set of watched tokens with their lock expiry timestamps.
Provides check_expiring_locks() for cron/background use.

Redis keys:
  rmi:liquidity:tracked      -> Set of token_ids (e.g. "solana:So111111...")
  rmi:liquidity:token:{id}   -> Hash with token_address, chain, lock_expiry, liquidity_amount, created_at

  token_id format: "{chain}:{token_address}"
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger("liquidity_watchdog")

# Redis key namespace
REDIS_NS = "rmi:liquidity"

# How far ahead (in hours) we consider "expiring soon"
EXPIRY_WINDOW_HOURS = 24

# Module-level Redis connection pool (reused across calls)
_redis_pool = None


def _get_redis():
    """Get a Redis client from the shared connection pool.

    Uses a module-level connection pool so connections are reused
    across calls rather than creating a new TCP connection each time.
    """
    import redis

    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return redis.Redis(connection_pool=_redis_pool)


def _token_id(token_address: str, chain: str) -> str:
    """Build a unique token ID for Redis keys."""
    return f"{chain}:{token_address}".lower()


def _token_hash_key(token_address: str, chain: str) -> str:
    """Redis hash key for a tracked token."""
    return f"{REDIS_NS}:token:{_token_id(token_address, chain)}"


def track_token(
    token_address: str,
    chain: str,
    lock_expiry_timestamp: int,
    liquidity_amount: float = 0.0,
) -> dict[str, Any]:
    """Register a token for liquidity expiry monitoring.

    Args:
        token_address: Contract/mint address of the token.
        chain: Blockchain name (e.g. 'solana', 'ethereum', 'bsc').
        lock_expiry_timestamp: Unix timestamp (seconds) when the lock expires.
        liquidity_amount: Locked liquidity amount in USD (or 0 if unknown).

    Returns:
        The stored token data dict.
    """
    r = _get_redis()
    token_id = _token_id(token_address, chain)
    hash_key = _token_hash_key(token_address, chain)

    data = {
        "token_address": token_address.lower(),
        "chain": chain.lower(),
        "lock_expiry": str(lock_expiry_timestamp),
        "liquidity_amount": str(liquidity_amount),
        "created_at": datetime.now(UTC).isoformat(),
    }

    r.hset(hash_key, mapping=data)
    r.sadd(f"{REDIS_NS}:tracked", token_id)

    logger.info(
        "Tracked token %s on %s, expiry=%d, liquidity=%.2f",
        token_address,
        chain,
        lock_expiry_timestamp,
        liquidity_amount,
    )

    return {
        "token_address": token_address.lower(),
        "chain": chain.lower(),
        "lock_expiry": lock_expiry_timestamp,
        "liquidity_amount": liquidity_amount,
        "created_at": data["created_at"],
    }


def untrack_token(token_address: str, chain: str) -> bool:
    """Remove a token from monitoring.

    Returns:
        True if the token was being tracked, False otherwise.
    """
    r = _get_redis()
    token_id = _token_id(token_address, chain)
    hash_key = _token_hash_key(token_address, chain)

    r.delete(hash_key)
    removed = r.srem(f"{REDIS_NS}:tracked", token_id)
    return removed > 0


def get_tracked_tokens() -> list[dict[str, Any]]:
    """List all currently tracked tokens with their stored data."""
    r = _get_redis()
    token_ids = r.smembers(f"{REDIS_NS}:tracked") or set()
    tokens = []

    for token_id in token_ids:
        hash_key = f"{REDIS_NS}:token:{token_id}"
        raw = r.hgetall(hash_key)
        if not raw:
            # Stale entry — clean up
            r.srem(f"{REDIS_NS}:tracked", token_id)
            continue

        tokens.append(
            {
                "token_address": raw.get("token_address", ""),
                "chain": raw.get("chain", ""),
                "lock_expiry": int(raw.get("lock_expiry", 0)),
                "liquidity_amount": float(raw.get("liquidity_amount", 0)),
                "created_at": raw.get("created_at", ""),
            }
        )

    return tokens


def check_expiring_locks() -> list[dict[str, Any]]:
    """Find all tracked tokens whose lock expiry is within EXPIRY_WINDOW_HOURS.

    Returns:
        List of dicts with:
          - token: token address
          - chain: chain name
          - lock_expiry: unix timestamp
          - hours_remaining: float hours until expiry
          - liquidity_amount: locked liquidity USD
    """
    now = datetime.now(UTC)
    cutoff = now + timedelta(hours=EXPIRY_WINDOW_HOURS)
    now_ts = now.timestamp()
    cutoff_ts = cutoff.timestamp()

    expiring: list[dict[str, Any]] = []
    tokens = get_tracked_tokens()

    for token_data in tokens:
        expiry = token_data.get("lock_expiry", 0)
        if expiry <= 0:
            continue

        # Check if expiry is within the window (and not already past)
        if now_ts < expiry <= cutoff_ts:
            hours_remaining = max(0.0, (expiry - now_ts) / 3600)
            expiring.append(
                {
                    "token": token_data["token_address"],
                    "chain": token_data["chain"],
                    "lock_expiry": expiry,
                    "hours_remaining": round(hours_remaining, 2),
                    "liquidity_amount": token_data.get("liquidity_amount", 0),
                }
            )

    # Sort by hours remaining (most urgent first)
    expiring.sort(key=lambda x: x["hours_remaining"])

    if expiring:
        logger.warning(
            "Found %d token(s) expiring within %d hours",
            len(expiring),
            EXPIRY_WINDOW_HOURS,
        )

    return expiring


def check_all_locks_summary() -> dict[str, Any]:
    """Get a summary of all tracked locks and their status.

    Returns a dict with counts and the list of expiring tokens.
    """
    tokens = get_tracked_tokens()
    now_ts = datetime.now(UTC).timestamp()

    already_expired = [t for t in tokens if t["lock_expiry"] <= now_ts]
    expiring_soon = check_expiring_locks()
    safe = [
        t
        for t in tokens
        if t["lock_expiry"] > now_ts
        and not any(e["token"] == t["token_address"] and e["chain"] == t["chain"] for e in expiring_soon)
    ]

    return {
        "total_tracked": len(tokens),
        "already_expired": len(already_expired),
        "expiring_soon": len(expiring_soon),
        "safe": len(safe),
        "expiring_tokens": expiring_soon,
    }
