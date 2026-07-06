"""ClickHouse wallet_memory.wallet_labels source - currently empty.

Schema (verified):
  address String
  chain_id String
  label_name String
  label_category String
  source String
  is_sanctioned UInt8 DEFAULT 0
  loaded_at DateTime DEFAULT now()
  label_subtype String DEFAULT ''

Returns [] until labels are imported. The plan is to bulk-import
the CSVs (etherscan_combined, ethereum labels, solana labels) via
the warehouse_etl jobs. Until then, this adapter is a no-op.
"""
from __future__ import annotations

import logging

from app.domain.labels.models import Label

log = logging.getLogger(__name__)


async def query_clickhouse_labels(address: str, chain: str = "ethereum") -> list[Label]:
    """Query ClickHouse wallet_memory.wallet_labels table.

    Currently returns [] - table is empty until CSVs are bulk-imported.
    Real implementation will use HTTP to clickhouse client over
    native protocol via app.core.duckdb_analytics.query_postgres-style attach.

    Chain mapping: clickhouse stores chain_id as String. We translate
    our 'ethereum'/'solana' names to the chain_id values used in the table.
    """
    # TODO: when wallet_memory.wallet_labels is populated, use:
    #   from app.core.duckdb_analytics import DuckDBAnalytics
    #   d = DuckDBAnalytics()
    #   rows = d.query_postgres(...)  # if we ever swap CH for Postgres
    #   OR use httpx against CH HTTP interface at http://localhost:8123
    return []
