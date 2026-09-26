#!/usr/bin/env python3
"""ClickHouse + Redis connection factories for the ingestion pipeline.

Both factories are best-effort: a missing driver or a refused connection yields
``None`` so the caller can degrade to ClickHouse-only (or abort) instead of
raising.
"""

from __future__ import annotations

import os
from typing import Any

from .logging_setup import logger

__all__ = ["get_ch_client", "get_redis_client"]


def _connect_http_client(ch_host: str, ch_port_raw: str, ch_user: str, ch_pass: str, ch_db: str) -> Any:
    """Connect via clickhouse-connect (HTTP). Raises on any failure."""
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=ch_host,
        port=int(ch_port_raw),
        username=ch_user,
        password=ch_pass,
        database=ch_db,
        connect_timeout=10,
    )
    client.query("SELECT 1")
    return client


def _connect_native_client(ch_host: str, ch_user: str, ch_pass: str, ch_db: str) -> Any:
    """Connect via clickhouse_driver (native protocol). Raises on any failure."""
    from clickhouse_driver import Client as CHClient

    native_port = int(os.getenv("CLICKHOUSE_NATIVE_PORT", "9000"))
    client = CHClient(
        host=ch_host,
        port=native_port,
        user=ch_user,
        password=ch_pass,
        database=ch_db,
        connect_timeout=5,
        send_receive_timeout=30,
    )
    client.execute("SELECT 1")
    return client


def get_ch_client() -> Any | None:
    """Get a ClickHouse client (sync), HTTP first then native driver.

    Env: CLICKHOUSE_HOST/PORT/USER/PASSWORD (CH_PASSWORD fallback) and
    CLICKHOUSE_NATIVE_PORT for the driver fallback. Database is wallet_memory.
    """
    ch_host = os.getenv("CLICKHOUSE_HOST", "localhost")
    ch_port_raw = os.getenv("CLICKHOUSE_PORT", "8123")
    ch_user = os.getenv("CLICKHOUSE_USER", "rmi")
    ch_pass = os.getenv("CLICKHOUSE_PASSWORD", os.getenv("CH_PASSWORD", ""))
    ch_db = "wallet_memory"

    try:
        client = _connect_http_client(ch_host, ch_port_raw, ch_user, ch_pass, ch_db)
        logger.info(f"ClickHouse connected via HTTP: {ch_host}:{ch_port_raw}/{ch_db} as {ch_user}")
        return client
    except ImportError:
        logger.debug("clickhouse-connect not available, trying clickhouse-driver")
    except Exception as e:
        logger.debug(f"clickhouse-connect failed: {e}")

    try:
        client = _connect_native_client(ch_host, ch_user, ch_pass, ch_db)
        native_port = os.getenv("CLICKHOUSE_NATIVE_PORT", "9000")
        logger.info(f"ClickHouse connected via native: {ch_host}:{native_port}/{ch_db} as {ch_user}")
        return client
    except ImportError:
        logger.error("Neither clickhouse-connect nor clickhouse-driver available")
        return None
    except Exception as e:
        logger.warning(f"ClickHouse native connection failed: {e}")
        return None


def _build_redis(sync_redis: Any) -> Any:
    """Build a Redis client from REDIS_URL or REDIS_HOST/PORT/PASSWORD/DB."""
    redis_url = os.getenv("REDIS_URL", "")
    if redis_url:
        return sync_redis.from_url(redis_url, decode_responses=True)
    r_host = os.getenv("REDIS_HOST", "localhost")
    r_port = int(os.getenv("REDIS_PORT", "6379"))
    r_pass = os.getenv("REDIS_PASSWORD", "")
    r_db = int(os.getenv("REDIS_DB", "0"))
    if r_pass:
        return sync_redis.Redis(host=r_host, port=r_port, password=r_pass, db=r_db, decode_responses=True)
    return sync_redis.Redis(host=r_host, port=r_port, db=r_db, decode_responses=True)


def get_redis_client() -> Any | None:
    """Get a Redis client, or None when unavailable (never raises)."""
    try:
        import redis as sync_redis

        client = _build_redis(sync_redis)
        client.ping()
        r_host = os.getenv("REDIS_HOST", "localhost")
        r_port = os.getenv("REDIS_PORT", "6379")
        r_db = os.getenv("REDIS_DB", "0")
        logger.info(f"Redis connected: {r_host}:{r_port}/{r_db}")
        return client
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")
        return None
