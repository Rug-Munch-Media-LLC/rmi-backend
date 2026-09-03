"""ClickHouse query layer for the risk_cluster package.

Every query is sync, bounded (``max_execution_time``) and degrades to ``[]`` /
``None`` on failure so the scan never crashes on missing cluster data. The
ClickHouse client is resolved lazily through the package shim at call time so
tests that patch ``risk_cluster._get_ch_client`` keep working end-to-end.
"""

from __future__ import annotations

from typing import Any, cast

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.engines.risk_cluster._constants import (
    _MAX_HOLDERS,
    _QUERY_TIMEOUT_SECONDS,
)

logger = get_logger(__name__)


def _client() -> Any | None:
    """Resolve the ClickHouse client through the package shim at call time.

    ``test_risk_report`` patches ``risk_cluster._get_ch_client`` end-to-end
    with a fake client; looking the name up lazily here keeps that patch point
    authoritative even though the query implementation now lives in this
    submodule.
    """
    from app.domains.scanner.engines.risk_cluster import _get_ch_client

    return _get_ch_client()


def _query_first_funders(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """First in-transfer per holder — funder, evidence tx.

    EVM:  ``evm_transfers`` in-rows for the token.
    Solana: native-SOL funders from ``solana_transfers`` (trace table) for
    holders of the mint (owners with positive deltas in
    ``solana_spl_balance_changes``). Graceful: when the trace table is
    empty for untracked mints, funder rows are absent and the leg degrades
    to an empty list (fail-open).
    """
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        o.owner AS holder,
                        argMin(f.from_address, f.block_timestamp) AS funder,
                        argMin(f.signature, f.block_timestamp) AS first_tx
                    FROM (
                        SELECT DISTINCT owner
                        FROM wallet_memory.solana_spl_balance_changes
                        WHERE mint = %(token)s
                          AND toFloat64OrZero(delta) > 0
                          AND owner != ''
                    ) o
                    LEFT JOIN (
                        SELECT from_address, to_address, block_timestamp, signature
                        FROM wallet_memory.solana_transfers
                        WHERE transfer_type IN ('native_sol', 'cpi_sol')
                          AND from_address != ''
                          AND to_address != ''
                    ) f ON f.to_address = o.owner
                    GROUP BY o.owner
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "limit": _MAX_HOLDERS},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_first_funders_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                to_address AS holder,
                argMin(from_address, block_timestamp) AS funder,
                argMin(tx_hash, block_timestamp) AS first_tx
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
            GROUP BY to_address
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "limit": _MAX_HOLDERS},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_first_funders_query_failed: %s", exc)
        return None


def _query_deployer_token_overlap(
    deployer: str,
    holders: list[str],
    token_address: str,
    chain: str,
    limit: int,
) -> list[tuple[Any, ...]] | None:
    """Other tokens by the same deployer + which of our holders co-hold them."""
    if not deployer or not holders:
        return None
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        t.mint AS token_address,
                        t.creator AS deployer,
                        arrayFilter(
                            h -> h IN (SELECT DISTINCT owner
                                       FROM wallet_memory.solana_spl_balance_changes
                                       WHERE mint = t.mint AND owner != ''),
                            %(holders)s
                        ) AS shared_holders
                    FROM (
                        SELECT mint, argMin(creator, block_timestamp) AS creator
                        FROM wallet_memory.solana_launches
                        WHERE creator = %(deployer)s
                          AND mint != %(token)s
                        GROUP BY mint
                        ORDER BY min(block_timestamp) DESC
                        LIMIT %(limit)s
                    ) t
                    WHERE length(shared_holders) >= 1
                    """,
                    {
                        "deployer": deployer,
                        "token": token_address,
                        "holders": holders,
                        "limit": limit,
                    },
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_shared_deployer_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                t.token_address,
                t.deployer,
                arrayFilter(
                    h -> h IN (SELECT to_address FROM wallet_memory.evm_transfers
                               WHERE token_address = t.token_address AND chain = %(chain)s),
                    %(holders)s
                ) AS shared_holders
            FROM (
                SELECT token_address, argMin(deployer, block_timestamp) AS deployer
                FROM wallet_memory.deployer_events
                WHERE chain = %(chain)s
                  AND deployer = %(deployer)s
                  AND token_address != %(token)s
                GROUP BY token_address
                ORDER BY min(block_timestamp) DESC
                LIMIT %(limit)s
            ) t
            WHERE length(shared_holders) >= 1
            """,
            {
                "deployer": deployer,
                "token": token_address,
                "chain": chain,
                "holders": holders,
                "limit": limit,
            },
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_shared_deployer_query_failed: %s", exc)
        return None


def _query_cross_token_overlap(
    holders: list[str],
    token_address: str,
    chain: str,
    limit: int,
) -> list[tuple[Any, ...]] | None:
    """Other tokens where >= 2 of our holders co-hold (cross-token clusters)."""
    if not holders:
        return None
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        mint AS token_address,
                        groupArray(owner) AS co_holders
                    FROM (
                        SELECT DISTINCT mint, owner
                        FROM wallet_memory.solana_spl_balance_changes
                        WHERE mint != %(token)s
                          AND owner IN %(holders)s
                          AND owner != ''
                    )
                    GROUP BY mint
                    HAVING length(co_holders) >= 2
                    ORDER BY length(co_holders) DESC
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "holders": holders, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_cross_token_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                token_address,
                groupArray(to_address) AS co_holders
            FROM (
                SELECT DISTINCT token_address, to_address
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND token_address != %(token)s
                  AND to_address IN %(holders)s
            )
            GROUP BY token_address
            HAVING length(co_holders) >= 2
            ORDER BY length(co_holders) DESC
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "holders": holders, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_cross_token_query_failed: %s", exc)
        return None


def _query_cross_token_funders(
    holders: list[str],
    token_address: str,
    chain: str,
    limit: int,
) -> list[tuple[Any, ...]] | None:
    """Funders of our holders that also funded wallets in OTHER launches.

    Returns rows ``(funder, other_token, [funded_wallets...])`` — the raw
    edges of the cross-token funder graph (leg 4). Solana uses
    ``solana_transfers`` native-SOL edges to owners of other mints.
    """
    if not holders:
        return None
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        f.from_address AS funder,
                        b.mint AS other_token,
                        groupArray(DISTINCT f.to_address) AS funded_wallets
                    FROM wallet_memory.solana_transfers f
                    JOIN wallet_memory.solana_spl_balance_changes b
                      ON b.owner = f.to_address AND b.mint != %(token)s
                    WHERE f.transfer_type IN ('native_sol', 'cpi_sol')
                      AND f.from_address != ''
                      AND f.to_address != ''
                      AND f.from_address IN (
                          SELECT DISTINCT from_address
                          FROM wallet_memory.solana_transfers
                          WHERE transfer_type IN ('native_sol', 'cpi_sol')
                            AND from_address != ''
                            AND to_address != ''
                      )
                    GROUP BY f.from_address, b.mint
                    ORDER BY length(funded_wallets) DESC
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_cross_token_funders_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                from_address AS funder,
                token_address AS other_token,
                groupArray(to_address) AS funded_wallets
            FROM (
                SELECT DISTINCT from_address, to_address, token_address
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND token_address != %(token)s
                  AND from_address IN (
                      SELECT DISTINCT from_address
                      FROM wallet_memory.evm_transfers
                      WHERE token_address = %(token)s AND chain = %(chain)s
                  )
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                              '0x000000000000000000000000000000000000dead')
                  AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
            )
            GROUP BY funder, other_token
            ORDER BY length(funded_wallets) DESC
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "holders": holders, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_cross_token_funders_query_failed: %s", exc)
        return None


def _query_first_buy_blocks(token_address: str, chain: str, limit: int) -> list[tuple[Any, ...]] | None:
    """First buy per holder (buyer, first_block, first_tx) — temporal leg.

    EVM: first DEX swap per holder from ``dex_events``. Solana: first
    positive balance delta per owner from ``solana_spl_balance_changes``
    (slot serves as the block anchor; tx_hash as evidence).
    """
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        if(owner != '', owner, token_account) AS buyer,
                        min(slot) AS first_block,
                        argMin(tx_hash, block_timestamp) AS first_tx
                    FROM wallet_memory.solana_spl_balance_changes
                    WHERE mint = %(token)s
                      AND toFloat64OrZero(delta) > 0
                      AND owner != ''
                    GROUP BY buyer
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_first_buy_blocks_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                buyer,
                min(block_number) AS first_block,
                argMin(tx_hash, block_timestamp) AS first_tx
            FROM (
                SELECT
                    if(recipient NOT IN ('', '0x0000000000000000000000000000000000000000'),
                       recipient, sender) AS buyer,
                    block_number,
                    block_timestamp,
                    tx_hash
                FROM wallet_memory.dex_events
                WHERE chain = %(chain)s
                  AND (token0 = %(token)s OR token1 = %(token)s)
                  AND type = 'swap'
                  AND sender NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead')
            )
            WHERE buyer != ''
            GROUP BY buyer
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_first_buy_blocks_query_failed: %s", exc)
        return None


def _query_deploy_info(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """Deploy anchor: (deploy_block_number, min(block_timestamp)).

    EVM reads ``deployer_events``; Solana reads ``solana_launches``
    (slot = block number).
    """
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            rows = client.execute(
                """
                SELECT
                    argMin(slot, block_timestamp) AS deploy_block,
                    min(block_timestamp) AS deploy_ts
                FROM wallet_memory.solana_launches
                WHERE mint = %(token)s
                """,
                {"token": token_address},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            return cast("list[tuple[Any, ...]] | None", rows)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_deploy_info_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                argMin(block_number, block_timestamp) AS deploy_block,
                min(block_timestamp) AS deploy_ts
            FROM wallet_memory.deployer_events
            WHERE token_address = %(token)s AND chain = %(chain)s
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_deploy_info_query_failed: %s", exc)
        return None


def _query_earliest_transfer(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """Fallback launch anchor: earliest transfer (block_number, block_timestamp).

    EVM reads ``evm_transfers``; Solana reads ``solana_spl_balance_changes``
    (min slot + min block_timestamp).
    """
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            rows = client.execute(
                """
                SELECT
                    min(slot) AS first_block,
                    min(block_timestamp) AS first_ts
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                """,
                {"token": token_address},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            return cast("list[tuple[Any, ...]] | None", rows)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_earliest_transfer_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                min(block_number) AS first_block,
                min(block_timestamp) AS first_ts
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s AND chain = %(chain)s
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_earliest_transfer_query_failed: %s", exc)
        return None


def _query_launch_window_transfers(
    token_address: str,
    chain: str,
    anchor_block: int,
    window_blocks: int,
    limit: int,
) -> list[tuple[Any, ...]] | None:
    """Launch-window buy rows: (wallet, amount, block, ts, tx_hash).

    EVM reads ``evm_transfers``; Solana reads ``solana_spl_balance_changes``
    (slot = block, delta = amount).
    """
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        if(owner != '', owner, token_account) AS wallet,
                        toFloat64OrZero(delta) AS amount,
                        slot AS block_number,
                        block_timestamp,
                        tx_hash
                    FROM wallet_memory.solana_spl_balance_changes
                    WHERE mint = %(token)s
                      AND slot >= %(anchor)s
                      AND slot <= %(anchor)s + %(window)s
                      AND toFloat64OrZero(delta) > 0
                      AND owner != ''
                    ORDER BY slot, block_timestamp
                    LIMIT %(limit)s
                    """,
                    {
                        "token": token_address,
                        "anchor": int(anchor_block),
                        "window": int(window_blocks),
                        "limit": limit,
                    },
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_launch_window_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                to_address AS wallet,
                toFloat64OrZero(value) AS amount,
                block_number,
                block_timestamp,
                tx_hash
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND block_number >= %(anchor)s
              AND block_number <= %(anchor)s + %(window)s
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
            ORDER BY block_number, block_timestamp
            LIMIT %(limit)s
            """,
            {
                "token": token_address,
                "chain": chain,
                "anchor": int(anchor_block),
                "window": int(window_blocks),
                "limit": limit,
            },
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_launch_window_query_failed: %s", exc)
        return None


def _query_wallets_with_prior_tx(
    wallets: list[str],
    chain: str,
    cutoff_ts: float | None,
) -> list[tuple[Any, ...]] | None:
    """Wallets that appear in ANY transfer before the launch anchor (prior history).

    EVM uses ``evm_transfers``; Solana uses ``solana_spl_balance_changes``
    (owner has balance-change rows before the cutoff).
    """
    if not wallets:
        return None
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            if cutoff_ts is None:
                rows = client.execute(
                    """
                    SELECT DISTINCT owner
                    FROM wallet_memory.solana_spl_balance_changes
                    WHERE owner IN %(wallets)s
                    LIMIT %(limit)s
                    """,
                    {"wallets": wallets, "limit": len(wallets)},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                )
            else:
                rows = client.execute(
                    """
                    SELECT DISTINCT owner
                    FROM wallet_memory.solana_spl_balance_changes
                    WHERE owner IN %(wallets)s
                      AND block_timestamp < toDateTime(%(cutoff)s)
                    LIMIT %(limit)s
                    """,
                    {"wallets": wallets, "cutoff": float(cutoff_ts), "limit": len(wallets)},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                )
            return cast("list[tuple[Any, ...]] | None", rows)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_prior_tx_solana_query_failed: %s", exc)
            return None
    try:
        if cutoff_ts is None:
            rows = client.execute(
                """
                SELECT DISTINCT to_address
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND to_address IN %(wallets)s
                LIMIT %(limit)s
                """,
                {"chain": chain, "wallets": wallets, "limit": len(wallets)},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
        else:
            rows = client.execute(
                """
                SELECT DISTINCT to_address
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND to_address IN %(wallets)s
                  AND block_timestamp < toDateTime(%(cutoff)s)
                LIMIT %(limit)s
                """,
                {"chain": chain, "wallets": wallets, "cutoff": float(cutoff_ts), "limit": len(wallets)},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_prior_tx_query_failed: %s", exc)
        return None


def _query_token_transfers(
    token_address: str,
    chain: str,
    lookback_hours: int,
    limit: int,
) -> list[tuple[Any, ...]] | None:
    """Recent token transfers (from, to, amount, ts) for wash-cycle detection.

    EVM reads ``evm_transfers``; Solana reads ``solana_transfers`` trace
    rows (native + SPL edges between real wallets — the wash-cycle DFS needs
    concrete from/to addresses, which ``solana_spl_balance_changes`` does
    not carry).
    """
    client = _client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        from_address,
                        to_address,
                        toFloat64OrZero(amount) AS amount,
                        block_timestamp
                    FROM wallet_memory.solana_transfers
                    WHERE mint = %(token)s
                      AND block_timestamp >= now() - INTERVAL %(lookback)s HOUR
                      AND from_address != ''
                      AND to_address != ''
                    ORDER BY block_timestamp
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "lookback": int(lookback_hours), "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_wash_transfers_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                from_address,
                to_address,
                toFloat64OrZero(value) AS amount,
                block_timestamp
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND block_timestamp >= now() - INTERVAL %(lookback)s HOUR
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead')
            ORDER BY block_timestamp
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "lookback": int(lookback_hours), "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_wash_transfers_query_failed: %s", exc)
        return None
