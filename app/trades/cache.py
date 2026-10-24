"""Redis cache for live trades — short TTL because the tape must stay fresh.

GeckoTerminal allows roughly 30 requests/minute per IP, so a per-(chain, token)
cache is not an optimisation, it is the rate-limit strategy. Cache failures are
never fatal: a miss simply re-fetches.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.core.redis import get_redis_async

logger = get_logger(__name__)

TRADES_TTL_S = 20
_KEY = "trades:v2:{chain}:{token}"


def _key(chain: str, token: str) -> str:
    return _KEY.format(chain=chain.lower(), token=token.lower())


async def read(chain: str, token: str) -> dict[str, Any] | None:
    """Return the cached payload for a token, or None on miss/error."""
    try:
        redis = get_redis_async()
        if not redis:
            return None
        raw = await redis.get(_key(chain, token))
        if not raw:
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception as exc:  # best-effort cache
        logger.debug("trades_cache_read_failed", error=type(exc).__name__)
        return None


async def write(chain: str, token: str, payload: dict[str, Any]) -> None:
    """Store the payload under a short TTL. Never raises."""
    try:
        redis = get_redis_async()
        if not redis:
            return
        await redis.setex(_key(chain, token), TRADES_TTL_S, json.dumps(payload))
    except Exception as exc:  # best-effort cache
        logger.debug("trades_cache_write_failed", error=type(exc).__name__)
