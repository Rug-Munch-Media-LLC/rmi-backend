"""Parquet Query MCP Tool - Query blockchain data from R2 Parquet data lake.

Provides an MCP tool to query blockchain data (transfers, swaps, deployments,
native_transfers) stored in Cloudflare R2 Parquet files via DuckDB's httpfs
extension. Supports filtering by address and block range across multiple EVM chains.

Port of the previously stranded ``feat/scanners-p4-8-cont`` branch work (2026-07-12).

Architecture:
  MCP tool  ->  parquet_query.py (async, thread-pool for DuckDB)
            ->  DuckDBAnalytics.query_remote_parquet()
            ->  DuckDB + httpfs -> S3/R2
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# Supported chains for the Parquet data lake
SUPPORTED_CHAINS: set[str] = {
    "ethereum",
    "bsc",
    "arbitrum",
    "polygon",
    "base",
}

# Supported table types
SUPPORTED_TABLES: set[str] = {
    "transfers",
    "swaps",
    "deployments",
    "native_transfers",
}

# Max rows to prevent abuse
MAX_LIMIT: int = 1000


async def query_parquet_mcp(
    chain: str,
    table: str,
    address: str | None = None,
    from_block: int | None = None,
    to_block: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """MCP tool: Query blockchain data from R2 Parquet data lake via DuckDB.

    Args:
        chain: Blockchain name (ethereum, bsc, arbitrum, polygon, base).
        table: Data table (transfers, swaps, deployments, native_transfers).
        address: Optional address filter (matched against from_address or to_address).
        from_block: Optional starting block number (inclusive).
        to_block: Optional ending block number (inclusive).
        limit: Max rows to return (default 100, max 1000).

    Returns:
        Dict with keys: rows, count, chain, table, truncated (and error on failure).
    """
    # -- Input validation -------------------------------------------------
    chain_key = chain.lower()
    if chain_key not in SUPPORTED_CHAINS:
        return {
            "error": f"Unsupported chain: {chain}. Supported: {', '.join(sorted(SUPPORTED_CHAINS))}",
            "rows": [],
            "count": 0,
        }

    table_key = table.lower()
    if table_key not in SUPPORTED_TABLES:
        return {
            "error": f"Unsupported table: {table}. Supported: {', '.join(sorted(SUPPORTED_TABLES))}",
            "rows": [],
            "count": 0,
        }

    limit = max(1, min(limit, MAX_LIMIT))

    # -- Build the query ---------------------------------------------------
    bucket_url = f"s3://rmi-parquet/{chain_key}/{table_key}/*/*/*.parquet"

    # Build WHERE clause from filters
    filters: list[str] = []
    if address:
        filters.append(f"(from_address = '{address}' OR to_address = '{address}')")
    if from_block is not None:
        filters.append(f"block_number >= {from_block}")
    if to_block is not None:
        filters.append(f"block_number <= {to_block}")

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    # S608: all inputs validated from constrained sets or clamped ints
    sql = f"SELECT * FROM read_parquet('{bucket_url}', union_by_name=true) {where_clause} ORDER BY block_number DESC LIMIT {limit}"  # noqa: S608

    # -- Execute in thread pool (DuckDB calls are synchronous) -------------
    def _execute() -> dict[str, Any]:
        from app.core.duckdb_analytics import DuckDBAnalytics

        try:
            db = DuckDBAnalytics()
            rows = db.query_remote_parquet(bucket_url, sql)
            return {
                "rows": rows,
                "count": len(rows),
                "chain": chain_key,
                "table": table_key,
                "truncated": len(rows) >= limit,
            }
        except Exception as e:
            log.error(
                "parquet_query_mcp_fail chain=%s table=%s err=%s: %s",
                chain_key,
                table_key,
                type(e).__name__,
                e,
            )
            return {
                "error": f"Query failed: {e!s}",
                "rows": [],
                "count": 0,
            }

    result = await asyncio.to_thread(_execute)
    return result
