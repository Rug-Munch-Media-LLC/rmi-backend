"""ClickHouse client factory for the trending feed.

Uses the native protocol on the port the deployed backend already uses
(``CLICKHOUSE_PORT=9000``); the HTTP driver is not on the prod image.
"""

from __future__ import annotations

from typing import Any

from clickhouse_driver import Client

from app.config import settings

DEFAULT_DB = "wallet_memory"


def get_client() -> Client:
    """Return a short-timeout native ClickHouse client."""
    return Client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=DEFAULT_DB,
        connect_timeout=3,
        send_receive_timeout=15,
        sync_request_timeout=15,
    )


def query_rows(sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    """Run one parameterised query and return raw rows."""
    client = get_client()
    try:
        return client.execute(sql, params)
    finally:
        client.disconnect()
