"""
Cross-Chain Trace — ClickHouse Queries
=======================================
"""

from __future__ import annotations

import os as _os
from typing import Any

from app.core.logging import get_logger

from .constants import _NATIVE_TOKEN_SYMBOLS

logger = get_logger("cross_chain_trace")


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client — shares config with lite_chain_indexer."""
    ch_host = _os.getenv("CH_HOST") or _os.getenv("CLICKHOUSE_HOST", "127.0.0.1")
    ch_port = int(_os.getenv("CH_PORT") or _os.getenv("CLICKHOUSE_PORT", "9000") or "9000")
    ch_user = _os.getenv("CH_USER") or _os.getenv("CLICKHOUSE_USER", "rmi")
    # NOTE: set CLICKHOUSE_PASSWORD (or CH_PASSWORD) env var to the ClickHouse password — never hardcode.
    ch_pass = _os.getenv("CH_PASSWORD") or _os.getenv("CLICKHOUSE_PASSWORD", "")
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass or None,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=15,
        )
        client.execute("SELECT 1")
        return client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError, AttributeError) as e:
        logger.warning("ClickHouse unavailable for cross_chain_trace: %s", e)
        return None


async def _run_query(fn):
    """Run a sync ClickHouse query in a thread pool."""
    import asyncio

    return await asyncio.to_thread(fn)


def _build_transfer_query(
    address: str,
    chain_filter: str | None = None,
    limit: int = 500,
) -> tuple[str, dict[str, Any]]:
    """Build SQL to find ALL transfers TO or FROM a given address."""
    params: dict[str, Any] = {
        "addr": address.lower(),
        "limit": limit,
    }
    where_parts = ["(from_address = %(addr)s OR to_address = %(addr)s)"]

    if chain_filter:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain_filter

    where_clause = " AND ".join(where_parts)

    return (
        f"""
        SELECT chain, block_number, tx_hash, from_address, to_address,
               token_address, value, block_timestamp, log_index,
               token_symbol, token_name
        FROM wallet_memory.evm_transfers
        WHERE {where_clause}
        ORDER BY block_timestamp DESC, block_number DESC
        LIMIT %(limit)s
        """,  # noqa: S608 - verified bound/allowlisted SQL construction
        params,
    )


async def _fetch_transfers(
    address: str,
    chain: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Fetch all EVM transfers involving the given address."""
    client = _get_ch_client()
    if client is None:
        return []

    sql, params = _build_transfer_query(address, chain, limit)

    def _query():
        try:
            return client.execute(sql, params)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("evm_transfers query failed: %s", exc)
            return []

    rows = await _run_query(_query)
    if not rows:
        return []

    transfers = []
    for row in rows:
        transfers.append(
            {
                "chain": row[0],
                "block_number": row[1],
                "tx_hash": row[2],
                "from_address": row[3],
                "to_address": row[4],
                "token_address": row[5],
                "value": row[6],
                "block_timestamp": (row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7])),
                "log_index": row[8],
                "token_symbol": row[9] if row[9] else _NATIVE_TOKEN_SYMBOLS.get(row[0]),
                "token_name": row[10],
            }
        )
    return transfers


async def _fetch_labels(
    addresses: set[str],
) -> dict[str, dict[str, Any]]:
    """Look up wallet labels for a set of addresses across all chains."""
    if not addresses:
        return {}

    client = _get_ch_client()
    if client is None:
        return {}

    from .constants import _LABEL_PRIORITY

    addr_list = list(addresses)
    placeholders = ", ".join(f"%({i})s" for i in range(len(addr_list)))
    params = {str(i): a for i, a in enumerate(addr_list)}

    sql = f"""
        SELECT address, label_name, label_category, is_sanctioned
        FROM wallet_memory.wallet_labels
        WHERE address IN ({placeholders})
        ORDER BY address
    """  # noqa: S608 - verified bound/allowlisted SQL construction

    def _query():
        try:
            return client.execute(sql, params)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("wallet_labels query failed: %s", exc)
            return []

    rows = await _run_query(_query)
    if not rows:
        return {}

    results: dict[str, dict[str, Any]] = {}
    for row in rows:
        addr = row[0].lower()
        category = row[2] or "unknown"
        priority = _LABEL_PRIORITY.get(category, 0)
        existing = results.get(addr)
        if existing is None or _LABEL_PRIORITY.get(existing.get("label_category", ""), 0) < priority:
            results[addr] = {
                "label_name": row[1] or "",
                "label_category": category,
                "is_sanctioned": bool(row[3]),
            }

    return results
