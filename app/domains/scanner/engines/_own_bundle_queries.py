"""ClickHouse query layer for own_bundle_detect (degodding split 2026-08-13).

All ``_query_*`` functions from ``own_bundle_detect.py`` live here. They are
pure data-access helpers: sync, bounded by ``max_execution_time``, and
graceful (return ``None`` / ``[]``) when ClickHouse is unreachable or the
query fails. ``own_bundle_detect.py`` re-exports every name so
``bd._query_*`` references in tests and callers keep working.

Note: ``_get_ch_client`` is imported from this module's own namespace — tests
patch ``bd._query_*`` directly (see test_scan_enhancements), not the client,
so this is safe.
"""

from __future__ import annotations

from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.domains.scanner.engines._common import _norm_addr
from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.engines._own_bundle_common import (
    _LAUNCH_WINDOW_BLOCKS,
    _QUERY_TIMEOUT_SECONDS,
    _get_ch_client,
)

logger = get_logger(__name__)


def _query_atomic_blocks(
    token_address: str,
    chain: str,
    client: Any | None = None,
) -> list[dict[str, Any]] | None:
    """Query for blocks where multiple wallets bought the token.

    A high number of buyers in a single block is a strong signal
    of coordinated launch bundling.

    Returns: [{"block_number": int, "buyer_count": int, "buy_count": int}, ...]
    """
    client = client or _get_ch_client()
    if client is None:
        return None

    try:
        if chain == "solana":
            rows = client.execute(
                """
                SELECT
                    slot AS block_number,
                    uniqExact(owner) AS buyer_count,
                    count() AS total_buys
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                  AND toFloat64OrZero(delta) > 0
                  AND owner != ''
                  AND block_timestamp >= now() - INTERVAL 7 DAY
                GROUP BY slot
                HAVING buyer_count >= 3
                ORDER BY buyer_count DESC
                LIMIT 20
                """,
                {"token": token_address},
            )
        else:
            rows = client.execute(
                """
                SELECT
                    block_number,
                    uniqExact(from_address) AS buyer_count,
                    count() AS total_buys
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                            '0x000000000000000000000000000000000000dead')
                  AND block_timestamp >= now() - INTERVAL 7 DAY
                GROUP BY block_number
                HAVING buyer_count >= 3
                ORDER BY buyer_count DESC
                LIMIT 20
                """,
                {"token": token_address, "chain": chain},
            )

        if not rows:
            return None

        return [
            {
                "block_number": r[0],
                "buyer_count": r[1],
                "total_buys": r[2],
            }
            for r in rows
        ]
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("atomic_blocks_query_failed: %s", exc)
        return None


def _query_common_funders(
    token_address: str,
    chain: str,
    client: Any | None = None,
) -> list[dict[str, Any]] | None:
    """Query for common funding sources among buyers.

    When multiple token buyers were funded by the same source wallet,
    it suggests coordinated bundling.

    Returns: [{"funder": str, "funded_count": int, "funded_addresses": [str]}]
    """
    client = client or _get_ch_client()
    if client is None:
        return None

    try:
        # First, get all buyers of this token
        if chain == "solana":
            buyers_rows = client.execute(
                """
                SELECT DISTINCT owner
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                  AND toFloat64OrZero(delta) > 0
                  AND owner != ''
                  AND block_timestamp >= now() - INTERVAL 7 DAY
                LIMIT 200
                """,
                {"token": token_address},
            )
        else:
            buyers_rows = client.execute(
                """
                SELECT DISTINCT from_address
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                            '0x000000000000000000000000000000000000dead')
                  AND block_timestamp >= now() - INTERVAL 7 DAY
                LIMIT 200
                """,
                {"token": token_address, "chain": chain},
            )

        if not buyers_rows or len(buyers_rows) < 3:
            return None

        buyer_addresses = [r[0] for r in buyers_rows]

        # Now find who funded these buyers (they received native token from a funder)
        if chain == "solana":
            # Solana: a funder is the owner with a negative delta in the
            # same tx (same tx_hash + mint) that credited the buyers.
            funder_rows = client.execute(
                """
                SELECT
                    f.owner AS funder,
                    uniqExact(b.owner) AS funded_count,
                    groupUniqArray(b.owner) AS funded_addresses_sample
                FROM wallet_memory.solana_spl_balance_changes AS b
                INNER JOIN wallet_memory.solana_spl_balance_changes AS f
                    ON f.tx_hash = b.tx_hash AND f.mint = b.mint
                WHERE b.owner IN %(buyers)s
                  AND toFloat64OrZero(b.delta) > 0
                  AND toFloat64OrZero(f.delta) < 0
                  AND f.owner != ''
                  AND f.owner != b.owner
                  AND b.block_timestamp >= now() - INTERVAL 14 DAY
                  AND b.block_timestamp <= now() - INTERVAL 1 HOUR
                  AND f.block_timestamp >= now() - INTERVAL 14 DAY
                GROUP BY f.owner
                HAVING funded_count >= 2
                ORDER BY funded_count DESC
                LIMIT 10
                """,
                {"buyers": buyer_addresses},
            )
        else:
            # For EVM: find common senders to these buyers
            funder_rows = client.execute(
                """
                SELECT
                    from_address AS funder,
                    uniqExact(to_address) AS funded_count,
                    groupUniqArray(to_address) AS funded_addresses_sample
                FROM wallet_memory.evm_transfers
                WHERE token_address = '0x0000000000000000000000000000000000000000'
                  AND chain = %(chain)s
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                            '0x000000000000000000000000000000000000dead',
                                            '0xdead000000000000000000000000000000000000')
                  AND to_address IN %(buyers)s
                  AND block_timestamp >= now() - INTERVAL 14 DAY
                  AND block_timestamp <= now() - INTERVAL 1 HOUR
                  AND value > '0'
                GROUP BY funder
                HAVING funded_count >= 2
                ORDER BY funded_count DESC
                LIMIT 10
                """,
                {"chain": chain, "buyers": buyer_addresses},
            )

        if not funder_rows:
            return None

        return [
            {
                "funder": r[0],
                "funded_count": r[1],
                "funded_addresses_sample": r[2][:10] if r[2] else [],
            }
            for r in funder_rows
        ]

    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("common_funders_query_failed: %s", exc)
        return None


def _query_holder_concentration(
    token_address: str,
    chain: str,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Query holder concentration data.

    Extreme top-holder concentration is a strong bundling signal.

    Returns: {
        "top3_pct": float,
        "top10_pct": float,
        "holder_count": int,
    }
    """
    client = client or _get_ch_client()
    if client is None:
        return None

    try:
        if chain == "solana":
            rows = client.execute(
                """
                SELECT
                    owner AS addr,
                    sum(toFloat64OrZero(delta)) AS balance
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                  AND owner != ''
                GROUP BY owner
                HAVING balance > 0
                ORDER BY balance DESC
                LIMIT 50
                """,
                {"token": token_address},
            )
        else:
            rows = client.execute(
                """
                SELECT
                    addr,
                    sum(net) AS balance
                FROM (
                    SELECT
                        to_address AS addr,
                        toFloat64OrZero(value) AS net
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND block_timestamp >= now() - INTERVAL 7 DAY
                      AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                    UNION ALL
                    SELECT
                        from_address AS addr,
                        -toFloat64OrZero(value) AS net
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND block_timestamp >= now() - INTERVAL 7 DAY
                      AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                )
                WHERE addr != ''
                GROUP BY addr
                HAVING balance > 0
                ORDER BY balance DESC
                LIMIT 50
                """,
                {"token": token_address, "chain": chain},
                # 2026-09-02 base-chain timeout fix: 7-day window + bounds
                # (was an unbounded full-table scan).
                settings={"max_execution_time": 10, "max_rows_to_read": 300_000_000},
            )

        if not rows:
            return None

        total_balance = sum(float(r[1] or 0) for r in rows)
        if total_balance <= 0:
            return None

        balances = [float(r[1] or 0) for r in rows]
        top3_pct = (
            sum(balances[:3]) / total_balance * 100 if len(balances) >= 3 else sum(balances) / total_balance * 100
        )
        top10_pct = (
            sum(balances[:10]) / total_balance * 100 if len(balances) >= 10 else sum(balances) / total_balance * 100
        )

        return {
            "top3_pct": round(top3_pct, 2),
            "top10_pct": round(top10_pct, 2),
            "holder_count": len(rows),
        }

    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("holder_concentration_query_failed: %s", exc)
        return None


def _query_launch_cluster(
    token_address: str,
    chain: str,
    window_blocks: int = _LAUNCH_WINDOW_BLOCKS,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Query launch-window buy cluster data for a token.

    Finds the token's deploy block (from ``deployer_events``, falling back to
    its first transfer block) then pulls every buy in the first ``window_blocks``
    blocks, annotated with:

      - ``is_first_tx`` — the buyer's first-ever on-chain tx is this token
        (no prior token history — fresh sniper farm signal),
      - ``deployer_funded`` — the buyer was funded by the deployer wallet
        (same-funder signal).

    Returns: {
        "deploy_block": int | None,
        "launch_buyers": [
            {"buyer": str, "block_number": int, "buy_count": int,
             "amount": float, "is_first_tx": bool, "deployer_funded": bool}
        ],
        "cluster_blocks": [{"block_number": int, "buyer_count": int}],
    }
    """
    client = client or _get_ch_client()
    if client is None:
        return None

    try:
        # ── 1. Deploy block (deployer_events first appearance) ──
        deploy_block: int | None = None
        deployer: str | None = None
        if chain != "solana":
            deploy_rows = client.execute(
                """
                SELECT block_number, deployer
                FROM wallet_memory.deployer_events
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND block_number > 0
                ORDER BY block_number ASC
                LIMIT 1
                """,
                {"token": token_address, "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            if deploy_rows and deploy_rows[0]:
                deploy_block = int(deploy_rows[0][0] or 0)
                deployer = _norm_addr(str(deploy_rows[0][1] or ""), chain) or None

        # ── 1b. Fallback: first transfer block ──
        if not deploy_block:
            if chain == "solana":
                min_rows = client.execute(
                    """
                    SELECT min(slot)
                    FROM wallet_memory.solana_spl_balance_changes
                    WHERE mint = %(token)s
                    """,
                    {"token": token_address},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                )
            else:
                min_rows = client.execute(
                    """
                    SELECT min(block_number)
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND block_timestamp >= now() - INTERVAL 7 DAY
                    """,
                    {"token": token_address, "chain": chain},
                    # 2026-09-02 base-chain timeout fix: the unwindowed
                    # min() scanned the whole table for tokens with no
                    # deployer_events row. 7-day window bounds it (~1.2s on
                    # base); launch detection only cares about recent
                    # launches anyway.
                    settings={"max_execution_time": 10, "max_rows_to_read": 300_000_000},
                )
            if min_rows and min_rows[0] and min_rows[0][0]:
                deploy_block = int(min_rows[0][0])

        if not deploy_block:
            return None

        start_block = deploy_block
        end_block = deploy_block + max(0, window_blocks - 1)

        # ── 2. Launch-window buys grouped by wallet ──
        if chain == "solana":
            buy_rows = client.execute(
                """
                SELECT
                    owner AS buyer,
                    slot AS block_number,
                    uniqExact(tx_hash) AS buy_count,
                    sum(toFloat64OrZero(delta)) AS amount
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                  AND toFloat64OrZero(delta) > 0
                  AND owner != ''
                  AND slot >= %(start)s
                  AND slot <= %(end)s
                GROUP BY owner, slot
                ORDER BY slot ASC
                LIMIT 300
                """,
                {"token": token_address, "start": start_block, "end": end_block},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
        else:
            buy_rows = client.execute(
                """
                SELECT
                    from_address AS buyer,
                    block_number,
                    count() AS buy_count,
                    sum(toFloat64OrZero(value)) AS amount
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                            '0x000000000000000000000000000000000000dead')
                  AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                            '0x000000000000000000000000000000000000dead')
                  AND block_number >= %(start)s
                  AND block_number <= %(end)s
                GROUP BY from_address, block_number
                ORDER BY block_number ASC
                LIMIT 300
                """,
                {"token": token_address, "chain": chain, "start": start_block, "end": end_block},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )

        if not buy_rows:
            return {"deploy_block": deploy_block, "launch_buyers": [], "cluster_blocks": []}

        buyers = list(dict.fromkeys(_norm_addr(str(r[0] or ""), chain) for r in buy_rows if r and r[0]))

        # ── 3. Per-buyer first-ever tx (any token, in/out) — prior-history check ──
        first_tx_map: dict[str, str] = {}
        if buyers:
            if chain == "solana":
                first_rows = client.execute(
                    """
                    SELECT
                        addr,
                        argMin(mint, block_timestamp) AS first_token
                    FROM (
                        SELECT owner AS addr, mint, block_timestamp
                        FROM wallet_memory.solana_spl_balance_changes
                        WHERE owner IN %(buyers)s
                          AND toFloat64OrZero(delta) > 0
                          AND owner != ''
                        UNION ALL
                        SELECT owner AS addr, mint, block_timestamp
                        FROM wallet_memory.solana_spl_balance_changes
                        WHERE owner IN %(buyers)s
                          AND toFloat64OrZero(delta) < 0
                          AND owner != ''
                    )
                    GROUP BY addr
                    """,
                    {"buyers": tuple(buyers)},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                )
            else:
                first_rows = client.execute(
                    """
                    SELECT
                        addr,
                        argMin(token_address, block_timestamp) AS first_token
                    FROM (
                        SELECT to_address AS addr, token_address, block_timestamp
                        FROM wallet_memory.evm_transfers
                        WHERE chain = %(chain)s
                          AND to_address IN %(buyers)s
                          AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                                    '0x000000000000000000000000000000000000dead')
                        UNION ALL
                        SELECT from_address AS addr, token_address, block_timestamp
                        FROM wallet_memory.evm_transfers
                        WHERE chain = %(chain)s
                          AND from_address IN %(buyers)s
                          AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                                    '0x000000000000000000000000000000000000dead')
                    )
                    GROUP BY addr
                    """,
                    {"buyers": tuple(buyers), "chain": chain},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                )
            for row in first_rows or []:
                if row and row[0]:
                    first_tx_map[_norm_addr(str(row[0]), chain)] = _norm_addr(str(row[1] or ""), chain)

        # ── 4. Deployer-funded launch buyers (same-funder signal) ──
        deployer_funded: set[str] = set()
        if deployer and buyers:
            funded_rows = client.execute(
                """
                SELECT DISTINCT to_address
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND from_address = %(deployer)s
                  AND to_address IN %(buyers)s
                  AND block_timestamp >= now() - INTERVAL 14 DAY
                """,
                {"deployer": deployer, "buyers": tuple(buyers), "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            for row in funded_rows or []:
                if row and row[0]:
                    deployer_funded.add(_norm_addr(str(row[0]), chain))

        # ── 5. Assemble result ──
        launch_buyers: list[dict[str, Any]] = []
        for r in buy_rows:
            buyer = _norm_addr(str(r[0] or ""), chain)
            if not buyer:
                continue
            launch_buyers.append(
                {
                    "buyer": buyer,
                    "block_number": int(r[1] or 0),
                    "buy_count": int(r[2] or 0),
                    "amount": round(float(r[3] or 0), 6),
                    "is_first_tx": first_tx_map.get(buyer) == token_address,
                    "deployer_funded": buyer in deployer_funded,
                }
            )

        block_counts: dict[int, int] = {}
        for b in launch_buyers:
            blk = int(b["block_number"])
            block_counts[blk] = block_counts.get(blk, 0) + 1
        cluster_blocks = [{"block_number": blk, "buyer_count": cnt} for blk, cnt in sorted(block_counts.items())]

        return {
            "deploy_block": deploy_block,
            "launch_buyers": launch_buyers,
            "cluster_blocks": cluster_blocks,
        }

    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("launch_cluster_query_failed: %s", exc)
        return None
