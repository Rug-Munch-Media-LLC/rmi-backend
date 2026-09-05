"""Input resolution + graceful-degradation helpers for the risk_cluster package.

Resolves the deployer / top holders from own data when not provided, resolves
the launch anchor (deploy block/ts, else earliest transfer), and builds the
``unavailable`` result so the scan never crashes on missing cluster data.
"""

from __future__ import annotations

from typing import Any, cast

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.domains.scanner.engines._common import run_sync, to_epoch
from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.engines.risk_cluster._constants import (
    _MAX_HOLDERS,
    _QUERY_TIMEOUT_SECONDS,
)
from app.domains.scanner.engines.risk_cluster._queries import (
    _client,
    _query_deploy_info,
    _query_earliest_transfer,
)

logger = get_logger(__name__)


async def _resolve_launch_anchor(token_address: str, chain_norm: str) -> tuple[int | None, float | None]:
    """Resolve (anchor_block, anchor_ts) from deployer_events, else earliest transfer.

    ``anchor_ts`` is an epoch float (may be None when the timestamp column is
    unavailable); it only feeds the optional first-tx-ever heuristic, so a
    missing value degrades gracefully.
    """
    try:
        rows = await run_sync(_query_deploy_info, token_address, chain_norm)
        if rows and rows[0] and rows[0][0]:
            block = int(rows[0][0])
            ts = to_epoch(rows[0][1])
            return block, ts
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as e:
        logger.error(f"_resolve_launch_anchor failed: {e}")
    try:
        rows = await run_sync(_query_earliest_transfer, token_address, chain_norm)
        if rows and rows[0] and rows[0][0]:
            return int(rows[0][0]), to_epoch(rows[0][1])
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as e:
        logger.error(f"_resolve_launch_anchor failed: {e}")
    return None, None


async def _resolve_inputs(
    token_address: str,
    chain_norm: str,
    *,
    deployer: str | None,
    holders: list[str] | None,
) -> dict[str, Any]:
    """Resolve deployer + top holders from own data when not provided."""
    resolved: dict[str, Any] = {"deployer": deployer, "holders": holders or []}

    if not deployer:
        try:
            rows = await run_sync(_query_deployer, token_address, chain_norm)
            if rows:
                resolved["deployer"] = str(rows[0] or "").lower() or None
        except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError):
            resolved["deployer"] = None

    if not resolved.get("holders"):
        try:
            rows = await run_sync(_query_top_holders, token_address, chain_norm, _MAX_HOLDERS)
            resolved["holders"] = [str(r[0] or "").lower() for r in (rows or []) if r and r[0]]
        except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError):
            resolved["holders"] = []

    return resolved


def _query_deployer(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """Earliest deployer for the token — EVM deployer_events / Solana solana_launches."""
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            rows = client.execute(
                """
                SELECT argMin(creator, block_timestamp)
                FROM wallet_memory.solana_launches
                WHERE mint = %(token)s
                  AND launch_signal = 1
                """,
                {"token": token_address},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            return cast("list[tuple[Any, ...]] | None", rows)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_deployer_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT argMin(deployer, block_timestamp)
            FROM wallet_memory.deployer_events
            WHERE token_address = %(token)s AND chain = %(chain)s
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_deployer_query_failed: %s", exc)
        return None


def _query_top_holders(token_address: str, chain: str, limit: int) -> list[tuple[Any, ...]] | None:
    """Top holders by net balance — EVM evm_transfers / Solana balance deltas."""
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            rows = client.execute(
                """
                SELECT
                    if(owner != '', owner, token_account) AS holder,
                    sum(toFloat64OrZero(delta)) AS bal
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                GROUP BY holder
                HAVING bal > 0
                ORDER BY bal DESC
                LIMIT %(limit)s
                """,
                {"token": token_address, "limit": limit},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            return cast("list[tuple[Any, ...]] | None", rows)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_top_holders_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT addr
            FROM (
                SELECT to_address AS addr, sum(toFloat64OrZero(value)) AS bal
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s AND chain = %(chain)s
                  AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                GROUP BY addr
                UNION ALL
                SELECT from_address AS addr, -sum(toFloat64OrZero(value)) AS bal
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s AND chain = %(chain)s
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                GROUP BY addr
            )
            WHERE addr != ''
            GROUP BY addr
            HAVING sum(bal) > 0
            ORDER BY sum(bal) DESC
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_top_holders_query_failed: %s", exc)
        return None


def _unavailable_result(
    token_address: str,
    chain_norm: str,
    reason: str,
    *,
    deployer: str | None = None,
    holders_analyzed: int = 0,
) -> dict[str, Any]:
    """Graceful degradation result (never raises / never blocks the scan)."""
    return {
        "check": "risk_cluster",
        "result": "unavailable",
        "source": "own",
        "token_address": token_address,
        "chain": chain_norm,
        "deployer": deployer,
        "holders_analyzed": holders_analyzed,
        "funder_tree": [],
        "shared_deployer_network": [],
        "cross_token_clusters": [],
        "cross_token_funders": [],
        "temporal_clusters": [],
        "sniper_patterns": [],
        "mev_bundles": [],
        "wash_trading": {"cycles": [], "self_swaps": [], "cycle_volume_ratio": 0.0, "wash_score": 0},
        "cluster_risk_score": 0,
        "cluster_risk_label": "LOW",
        "cluster_risk_description": f"Cluster data unavailable: {reason}",
        "component_scores": {},
        "note": reason,
    }
