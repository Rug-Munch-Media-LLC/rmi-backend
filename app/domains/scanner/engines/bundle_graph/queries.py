"""ClickHouse reads for the bundle graph.

All reads are bounded by the token mint (or by ``chain`` + a block range for
native value transfers) so a scan cannot turn into a table scan. Every
function returns plain dicts and degrades to ``[]`` / ``None`` on any data
error -- the caller distinguishes "no bundle" from "no data" via the
``notes`` it records, never by guessing.
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.engines._own_bundle_common import _new_ch_client

logger = get_logger(__name__)

#: Wrapped-SOL mint, used to find native value transfers on Solana.
SOL_MINT = "So11111111111111111111111111111111111111112"

_TRADE_SQL = """
SELECT tx_hash, block_number, toUnixTimestamp(block_timestamp) AS ts,
       sender, recipient,
       token0, token1, amount0_in, amount1_in, amount0_out, amount1_out,
       price_usd
FROM wallet_memory.dex_events
WHERE chain = %(chain)s
  AND (token0 = %(mint)s OR token1 = %(mint)s)
  AND block_number BETWEEN %(lo)s AND %(hi)s
LIMIT 5000
"""


def _rows(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Run one query and return dict rows (``[]`` on any failure)."""
    client = _new_ch_client()
    if client is None:
        return []
    try:
        result = client.execute(sql, params, with_column_types=True)
        rows, cols = result
        names = [c[0] for c in cols]
        return [dict(zip(names, row, strict=False)) for row in rows]
    except Exception as exc:
        logger.debug("bundle_graph_query_failed error=%s", str(exc)[:160])
        return []
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def fetch_launch_slot(token: str, chain: str) -> int | None:
    """The earliest observed trading slot for the token, or ``None``."""
    rows = _rows(
        """
        SELECT min(block_number) AS slot
        FROM wallet_memory.dex_events
        WHERE chain = %(chain)s AND (token0 = %(mint)s OR token1 = %(mint)s)
        """,
        {"chain": chain, "mint": token},
    )
    if not rows:
        return None
    slot = rows[0].get("slot")
    return int(slot) if slot else None


def fetch_declared_launch(token: str, chain: str) -> dict[str, Any] | None:
    """Indexer-recorded launch (slot / creator) when the launch table has it."""
    rows = _rows(
        """
        SELECT slot, creator, block_timestamp, venue
        FROM wallet_memory.solana_launches
        WHERE mint = %(mint)s AND chain = %(chain)s
        ORDER BY slot ASC LIMIT 1
        """,
        {"mint": token, "chain": chain},
    )
    return rows[0] if rows else None


def fetch_window_trades(
    token: str,
    chain: str,
    launch_slot: int,
    window_slots: int,
) -> list[dict[str, Any]]:
    """Trade legs inside the launch window for one mint."""
    return _rows(
        _TRADE_SQL,
        {"chain": chain, "mint": token, "lo": launch_slot, "hi": launch_slot + window_slots},
    )


def fetch_supply_raw(token: str, chain: str) -> tuple[int | None, str]:
    """Estimate circulating supply (raw units) from observed SPL balances.

    Returns ``(supply, source)``. The source string is reported verbatim in
    the payload: a denominator we cannot prove is never presented as fact.
    """
    rows = _rows(
        """
        SELECT sum(mx) AS supply FROM (
            SELECT token_account, max(toUInt256OrZero(post_amount)) AS mx
            FROM wallet_memory.solana_spl_balance_changes
            WHERE mint = %(mint)s
            GROUP BY token_account
        )
        """,
        {"mint": token},
    )
    if not rows:
        return None, "unavailable"
    supply = rows[0].get("supply")
    if not supply:
        return None, "unavailable"
    return int(supply), "observed_spl_balances"


def fetch_value_transfers(
    buyers: list[str],
    chain: str,
    before_slot: int,
) -> list[dict[str, Any]]:
    """Native-value transfers into the buyer wallets before their first buy."""
    if not buyers:
        return []
    if chain == "solana":
        return _rows(
            """
            SELECT from_address, to_address, amount, slot,
                   toUnixTimestamp(block_timestamp) AS ts, signature
            FROM wallet_memory.solana_transfers
            WHERE transfer_type = 'native_sol'
              AND to_address IN %(buyers)s
              AND slot <= %(before)s
            """,
            {"buyers": tuple(buyers), "before": before_slot},
        )
    return _rows(
        """
        SELECT from_address, to_address, value_wei AS amount, block_number AS slot,
               toUnixTimestamp(block_timestamp) AS ts, tx_hash AS signature
        FROM wallet_memory.internal_transfers
        WHERE chain = %(chain)s
          AND to_address IN %(buyers)s
          AND block_number BETWEEN %(lo)s AND %(before)s
        LIMIT 2000
        """,
        {
            "chain": chain,
            "buyers": tuple(buyers),
            "before": before_slot,
            "lo": max(0, before_slot - 500_000),
        },
    )


def fetch_first_activity(
    buyers: list[str],
    chain: str,
    before_slot: int,
    lookback_slots: int = 250_000,
) -> list[dict[str, Any]]:
    """Each buyer wallet's earliest recorded transaction before the launch.

    Used only as a *weak* co-occurrence signal when value transfers are not
    indexed for the chain.
    """
    if not buyers or chain != "solana":
        return []
    return _rows(
        """
        SELECT address, min(slot) AS slot, any(tx_signature) AS tx,
               any(involved_accounts) AS involved
        FROM wallet_memory.address_activity
        WHERE chain = 'solana'
          AND address IN %(buyers)s
          AND slot BETWEEN %(lo)s AND %(before)s
        GROUP BY address
        """,
        {
            "buyers": tuple(buyers),
            "before": before_slot,
            "lo": max(0, before_slot - lookback_slots),
        },
    )
