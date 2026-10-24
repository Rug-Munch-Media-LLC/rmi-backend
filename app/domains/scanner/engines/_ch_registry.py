"""Per-thread ClickHouse client registry.

``clickhouse_driver.Client`` is synchronous and does not tolerate concurrent
use of one connection, yet every scanner engine reaches it through
``asyncio.to_thread``. Caching one client per worker thread — and guarding the
first creation with a lock — removes the check-then-act race that closed a
socket underneath another thread (``OSError: [Errno 9] Bad file descriptor``,
106x/day in the scan worker).
"""

from __future__ import annotations

import os
import threading
from typing import Any

from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)

_local = threading.local()
_lock = threading.Lock()


def _create_clickhouse_client(key: str) -> Any | None:
    """Open and verify one ClickHouse connection for ``wallet_memory``."""
    try:
        from clickhouse_driver import Client as CHClient
        from clickhouse_driver.errors import Error as CHDriverError
    except ImportError:
        logger.warning("%s: clickhouse_driver not installed", key)
        return None

    host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    password = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""

    try:
        client = CHClient(
            host=host,
            port=port,
            user=user,
            password=password,
            database="wallet_memory",
            connect_timeout=3,
            send_receive_timeout=15,
        )
        client.execute("SELECT 1")
    except (ConnectionError, TimeoutError, RuntimeError, OSError, AttributeError, CHDriverError) as exc:
        logger.warning("%s ClickHouse unavailable: %s", key, str(exc).splitlines()[0][:200])
        return None

    logger.info("%s ClickHouse connected: %s:%s/wallet_memory", key, host, port)
    return client


def thread_client(key: str, factory: Any = _create_clickhouse_client) -> Any | None:
    """Return this thread's cached client for ``key``, creating it at most once."""
    cache: dict[str, Any] = getattr(_local, "clients", None) or {}
    _local.clients = cache
    if key in cache:
        return cache[key]
    with _lock:
        if key in cache:
            return cache[key]
        client = factory(key)
        if client is None:
            return None
        cache[key] = client
        return client
