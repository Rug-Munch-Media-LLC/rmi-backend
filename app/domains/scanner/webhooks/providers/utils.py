"""Shared utilities for webhook event providers.

ClickHouse connection, cursor persistence (Redis), and helpers.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.core.redis import get_redis_async

log = get_logger(__name__)

# Lazy ClickHouse client (shared across all providers)
_ch_client: Any = None


def get_clickhouse() -> Any | None:
    """Lazy-init ClickHouse client. Returns None if unavailable.

    Uses clickhouse-driver (native protocol, port 9000) matching
    the wallet_memory.storage pattern.
    """
    global _ch_client
    if _ch_client is not None:
        return _ch_client
    try:
        import os

        import clickhouse_driver

        host = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
        port = int(os.getenv("CLICKHOUSE_PORT", "9000"))
        user = os.getenv("CLICKHOUSE_USER", "rmi")
        password = os.getenv("CLICKHOUSE_PASSWORD", "")
        database = os.getenv("CLICKHOUSE_DATABASE", "wallet_memory")

        _ch_client = clickhouse_driver.Client(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=5,
            send_receive_timeout=30,
        )
        _ch_client.execute("SELECT 1")
        log.info("webhooks_clickhouse_connected", host=host, port=port, db=database)
        return _ch_client
    except (ValueError, ConnectionError, RuntimeError) as exc:
        log.warning("webhooks_clickhouse_unavailable", error=str(exc))
        _ch_client = None
        return None


async def load_cursor(key: str) -> dict[str, Any] | None:
    """Load a poll cursor from Redis."""
    r = get_redis_async()
    if r is None:
        return None
    try:
        raw = await r.get(key)
        if raw:
            return json.loads(raw)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        log.warning("cursor_load_failed", key=key, error=str(exc))
    return None


async def save_cursor(key: str, cursor: dict[str, Any]) -> None:
    """Save a poll cursor to Redis with 7-day expiry."""
    r = get_redis_async()
    if r is None:
        return
    try:
        await r.set(key, json.dumps(cursor), ex=604800)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        log.warning("cursor_save_failed", key=key, error=str(exc))
