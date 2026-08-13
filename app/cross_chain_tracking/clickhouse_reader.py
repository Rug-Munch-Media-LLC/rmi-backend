"""
ClickHouse Reader — queries deployer_events from chain-indexer's ClickHouse.
============================================================================
Reads from the chain-indexer's ClickHouse instance (could be same or different
from wallet_memory's ClickHouse). The deployer_events table is created by
chain-indexer/src/clickhouse.ts with schema:

  deployer_events (
    deployer       String,
    token_address  String,
    chain          String,
    block_number   UInt64,
    block_timestamp DateTime,
    tx_hash        String,
    type           String,       -- 'token', 'pool', 'factory'
    factory        Nullable(String),
    ingested_at    DateTime
  ) ENGINE = MergeTree
  ORDER BY (chain, block_number)
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger

from .models import DeployerEvent

logger = get_logger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
# The chain-indexer ClickHouse may share a DB or be separate. We use the
# same CLICKHOUSE_* env vars but with optional CHAIN_INDEXER_DB override.
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "9000"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DB = os.getenv("CHAIN_INDEXER_CLICKHOUSE_DB", "lite_chain_indexer")

# Maximum events to fetch per query
MAX_BATCH_SIZE = 100_000


def _get_client() -> Any | None:
    """Lazy-init ClickHouse client for chain-indexer data."""
    try:
        import clickhouse_driver

        client = clickhouse_driver.Client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            user=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database=CLICKHOUSE_DB,
            connect_timeout=10,
            send_receive_timeout=60,
        )
        client.execute("SELECT 1")
        return client
    except ImportError:
        logger.warning("clickhouse_driver not installed — deployer queries disabled")
    except Exception as e:
        logger.warning("ClickHouse (chain-indexer) connection failed: %s", e)
    return None


async def fetch_multi_chain_deployers(
    min_chains: int = 2,
    min_total_deployments: int = 1,
    chain_filter: list[str] | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Find deployers active on N+ chains via ClickHouse GROUP BY.

    Args:
        min_chains: Minimum chains to qualify as multi-chain (default: 2)
        min_total_deployments: Minimum total deployments across all chains
        chain_filter: Optional list of chains to restrict query to
        limit: Max results

    Returns:
        List of {deployer, chains, chain_count, total_deployments,
                 deployments_by_chain, first_seen, last_seen}
    """
    client = _get_client()
    if not client:
        return []

    where_clause = "1=1"
    params: dict[str, Any] = {}
    if chain_filter:
        # Bind the chain list as a parameter — never interpolate user input
        where_clause = "chain IN %(chains)s"
        params["chains"] = tuple(chain_filter)

    query = f"""
    SELECT
        deployer,
        groupUniqArray(chain) AS chains,
        uniqExact(chain) AS chain_count,
        count() AS total_deployments,
        min(block_timestamp) AS first_seen,
        max(block_timestamp) AS last_seen
    FROM deployer_events
    WHERE {where_clause}
    GROUP BY deployer
    HAVING chain_count >= %(min_chains)s
       AND total_deployments >= %(min_total)s
    ORDER BY total_deployments DESC, chain_count DESC
    LIMIT %(limit)s
    """  # noqa: S608 - verified bound/allowlisted SQL construction

    try:
        import asyncio

        def _run() -> list[dict[str, Any]]:
            rows = client.execute(
                query,
                {
                    "min_chains": min_chains,
                    "min_total": min_total_deployments,
                    "limit": limit,
                    **params,
                },
            )
            results = []
            for row in rows:
                deployer, chains_json, chain_count, total, first_ts, last_ts = row
                # chains comes as JSON array from ClickHouse
                chains = chains_json if isinstance(chains_json, list) else [chains_json]
                results.append(
                    {
                        "deployer": deployer.lower(),
                        "chains": chains,
                        "chain_count": chain_count,
                        "total_deployments": total,
                        "first_seen": str(first_ts) if first_ts else "",
                        "last_seen": str(last_ts) if last_ts else "",
                    }
                )
            return results

        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("ClickHouse multi-chain deployer query failed: %s", e)
        return []


async def fetch_deployments_for_deployer(
    deployer: str,
    chain_filter: list[str] | None = None,
    limit: int = 200,
) -> list[DeployerEvent]:
    """Fetch all deployer_events for a specific address.

    Args:
        deployer: Wallet address to query
        chain_filter: Optional chain restriction
        limit: Max events

    Returns:
        List of DeployerEvent objects
    """
    client = _get_client()
    if not client:
        return []

    where = "deployer = %(deployer)s"
    params: dict[str, Any] = {"deployer": deployer.lower(), "limit": limit}

    if chain_filter:
        # Bind the chain list as a parameter — never interpolate user input
        where += " AND chain IN %(chains)s"
        params["chains"] = tuple(chain_filter)

    query = f"""
    SELECT deployer, token_address, chain, block_number, block_timestamp,
           tx_hash, type, factory, ingested_at
    FROM deployer_events
    WHERE {where}
    ORDER BY block_timestamp DESC
    LIMIT %(limit)s
    """  # noqa: S608 - verified bound/allowlisted SQL construction

    try:
        import asyncio

        def _run() -> list[DeployerEvent]:
            rows = client.execute(query, params)
            return [
                DeployerEvent(
                    deployer=r[0].lower(),
                    token_address=r[1].lower(),
                    chain=r[2],
                    block_number=r[3] or 0,
                    block_timestamp=str(r[4]) if r[4] else "",
                    tx_hash=r[5] or "",
                    type=r[6] or "token",
                    factory=r[7].lower() if r[7] else None,
                    ingested_at=str(r[8]) if r[8] else "",
                )
                for r in rows
            ]

        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("ClickHouse deployer events fetch failed: %s", e)
        return []


async def fetch_deployment_counts_by_chain(limit: int = 20) -> list[dict[str, Any]]:
    """Get deployment counts per chain (for dashboard/metrics)."""
    client = _get_client()
    if not client:
        return []

    query = """
    SELECT chain, count() AS deployments, uniqExact(deployer) AS unique_deployers
    FROM deployer_events
    GROUP BY chain
    ORDER BY deployments DESC
    LIMIT %(limit)s
    """

    try:
        import asyncio

        def _run() -> list[dict[str, Any]]:
            rows = client.execute(query, {"limit": limit})
            return [{"chain": r[0], "deployments": r[1], "unique_deployers": r[2]} for r in rows]

        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("ClickHouse deployment counts query failed: %s", e)
        return []


async def fetch_chains_with_data() -> list[str]:
    """Get list of chains that have deployer_events data."""
    rows = await fetch_deployment_counts_by_chain(limit=100)
    return [r["chain"] for r in rows if r.get("deployments", 0) > 0]


def get_clickhouse_reader() -> str:
    """Return database connection info for diagnostics."""
    return f"{CLICKHOUSE_USER}@{CLICKHOUSE_HOST}:{CLICKHOUSE_PORT}/{CLICKHOUSE_DB}"
