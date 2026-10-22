"""x402 exact — replay protection.

Two independent keys are claimed before a resource is served:

* the payment id / nonce issued in the challenge — one challenge, one use;
* the settled transaction hash — one on-chain payment, one delivery.

Claiming is atomic (Redis SET NX). Without Redis we fall back to a
process-local store so a single-instance deployment still refuses replays.
"""

from __future__ import annotations

import os
import time
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

_MEMORY: dict[str, float] = {}
_TTL_SECONDS = 86_400


def _redis() -> Any | None:
    """Return an async Redis client, or None when Redis is unavailable."""
    try:
        import redis.asyncio as redis_lib

        return redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
            decode_responses=True,
        )
    except (ImportError, ValueError) as exc:  # pragma: no cover — optional dep
        log.warning(f"x402_exact_redis_unavailable: {exc}")
        return None


def _sweep() -> None:
    """Drop expired process-local claims."""
    now = time.time()
    for key in [k for k, exp in _MEMORY.items() if exp <= now]:
        _MEMORY.pop(key, None)


async def claim(kind: str, value: str) -> bool:
    """Atomically claim ``kind:value``. True when this caller won the claim."""
    if not value:
        return False
    key = f"x402:exact:{kind}:{value}"
    client = _redis()
    if client is not None:
        try:
            won = await client.set(key, "1", nx=True, ex=_TTL_SECONDS)
            return bool(won)
        except (ConnectionError, OSError, ValueError) as exc:
            log.warning(f"x402_exact_replay_redis_failed: {exc}")
    _sweep()
    if key in _MEMORY:
        return False
    _MEMORY[key] = time.time() + _TTL_SECONDS
    return True


async def release(kind: str, value: str) -> None:
    """Undo a claim — used when settlement fails after a claim was taken."""
    if not value:
        return
    key = f"x402:exact:{kind}:{value}"
    _MEMORY.pop(key, None)
    client = _redis()
    if client is None:
        return
    try:
        await client.delete(key)
    except (ConnectionError, OSError, ValueError) as exc:
        log.warning(f"x402_exact_replay_release_failed: {exc}")
