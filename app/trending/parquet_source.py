"""Optional B2 Parquet overlay read through DuckDB.

Enabled only when ``TRENDING_PARQUET_GLOB`` is set (local path, glob, or
``s3://bucket/prefix/*.parquet``). Any failure — missing extension, network,
schema drift — yields ``[]`` so the ClickHouse feed is never blocked.
"""

from __future__ import annotations

import os
from typing import Any

import duckdb

from app.core.logging import get_logger

logger = get_logger("trending.parquet")

ENV_VAR = "TRENDING_PARQUET_GLOB"

SELECT_SQL = """
SELECT chain, token, symbol, name, price, market_cap, liquidity, volume_usd,
       trades, buys, sells, price_change_pct, bonding_progress
FROM read_parquet('{glob}')
WHERE chain IS NOT NULL AND token IS NOT NULL
"""

FIELDS = (
    "chain",
    "token",
    "symbol",
    "name",
    "price",
    "market_cap",
    "liquidity",
    "volume_usd",
    "trades",
    "buys",
    "sells",
    "price_change_pct",
    "bonding_progress",
)


def fetch_parquet_rows() -> list[dict[str, Any]]:
    """Read the configured Parquet overlay, or [] when unset/broken."""
    glob = os.environ.get(ENV_VAR, "").strip()
    if not glob:
        return []
    try:
        con = duckdb.connect(":memory:")
        try:
            con.execute("SET memory_limit = '512MB'")
            rows = con.execute(SELECT_SQL.format(glob=glob)).fetchall()
        finally:
            con.close()
    except Exception as exc:  # fail-open by design
        logger.warning("trending parquet overlay skipped: %s", exc)
        return []
    return [dict(zip(FIELDS, row, strict=False)) for row in rows]
