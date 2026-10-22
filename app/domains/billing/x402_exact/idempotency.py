"""x402 exact — idempotency for paid calls.

A retried request carrying the same ``Idempotency-Key`` returns the stored
response instead of charging again. The key is bound to the caller and the
resource so a key cannot be replayed across endpoints.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

_TTL = 86_400
_MEMORY: dict[str, str] = {}


def _key(idem_key: str, handle: str, identity: str) -> str:
    """Stable storage key for (idempotency key, resource, caller)."""
    digest = hashlib.sha256(f"{idem_key}:{handle}:{identity}".encode()).hexdigest()
    return f"x402:exact:idem:{digest}"


def _redis():
    """Async Redis client, or None when unavailable."""
    try:
        import redis.asyncio as redis_lib

        return redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
            decode_responses=True,
        )
    except (ImportError, ValueError):
        return None


async def lookup(idem_key: str, handle: str, identity: str) -> dict[str, Any] | None:
    """Return a previously stored response body, or None."""
    if not idem_key:
        return None
    store_key = _key(idem_key, handle, identity)
    client = _redis()
    if client is not None:
        try:
            raw = await client.get(store_key)
            return json.loads(raw) if raw else None
        except (ConnectionError, OSError, ValueError) as exc:
            log.warning(f"x402_exact_idem_read_failed: {exc}")
    raw = _MEMORY.get(store_key)
    return json.loads(raw) if raw else None


async def store(idem_key: str, handle: str, identity: str, body: dict[str, Any]) -> None:
    """Persist a response body under the caller's idempotency key."""
    if not idem_key:
        return
    store_key = _key(idem_key, handle, identity)
    raw = json.dumps(body, separators=(",", ":"), default=str)
    client = _redis()
    if client is not None:
        try:
            await client.set(store_key, raw, ex=_TTL)
            return
        except (ConnectionError, OSError, ValueError) as exc:
            log.warning(f"x402_exact_idem_write_failed: {exc}")
    _MEMORY[store_key] = raw
