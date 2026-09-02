"""Sniper detection — wallets that bought within the first blocks of launch.

Phanes-parity signal (2026-09-02): "snipers" are bots that buy within
seconds of a token going live (first 5 blocks/slots). Computed from OWN
ClickHouse data:

  * EVM     — ``wallet_memory.evm_transfers``: a buy is a transfer row with
              ``to_address = buyer`` and ``token_address = token``.
  * Solana  — ``wallet_memory.solana_spl_balance_changes``: a buy is a
              signed balance row with ``owner = buyer``, ``mint = token``
              and ``delta > 0``.

``launch_block`` resolution (when not supplied by the caller):
``wallet_memory.deployer_events`` first appearance of the token (EVM), or
``min(slot)`` over the token's balance rows (Solana) — the same fallback
``_own_bundle_queries._query_launch_cluster`` uses.

``sniper_pct`` is the share of ALL distinct buyers of the token that
sniped the launch window (bounded query, recent window only — see the
large-token guard).

Fail-open by contract: ClickHouse outage → ``{"result": "error"}``.
Never raises.
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines._chain_data import get_chain_spec, normalize_addr
from app.domains.scanner.engines._common import run_sync
from app.domains.scanner.engines._indexer_port import new_ch_client as _new_ch_client
from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)

_QUERY_TIMEOUT_S = 10
# A "sniper" bought within this many blocks/slots of the launch block.
_SNIPER_WINDOW_BLOCKS = 5
# Large-token guard (mirrors first_buyers.py): established tokens have
# millions of transfer rows and no rug signal in their launch window.
_LARGE_TOKEN_RECENT_TRANSFERS = 100_000


def _resolve_launch_block(token_address: str, chain: str, client: Any) -> int | None:
    """Sync ClickHouse query: launch block for a token (best-effort)."""
    if client is None:
        return None
    spec = get_chain_spec(chain)
    token = normalize_addr(token_address, chain)
    params: dict[str, Any] = {"token": token}
    if not spec.is_solana:
        params["chain"] = spec.chain
        rows = client.execute(
            """
            SELECT min(block_number)
            FROM wallet_memory.deployer_events
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND block_number > 0
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_S},
        )
    else:
        rows = client.execute(
            f"""
            SELECT min({spec.block_col})
            FROM {spec.transfers_table}
            WHERE {spec.addr_col} = %(token)s
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_S},
        )
    if rows and rows[0] and rows[0][0]:
        return int(rows[0][0])
    return None


def _query_snipers(
    token_address: str,
    chain: str,
    launch_block: int,
    client: Any,
) -> dict[str, Any] | None:
    """Sync ClickHouse query: sniper cohort + total-buyer denominator."""
    if client is None:
        return None
    spec = get_chain_spec(chain)
    token = normalize_addr(token_address, chain)
    params: dict[str, Any] = {
        "token": token,
        "lo": launch_block,
        "hi": launch_block + _SNIPER_WINDOW_BLOCKS - 1,
    }
    chain_frag = "" if spec.is_solana else spec.chain_filter
    if not spec.is_solana:
        params["chain"] = spec.chain

    if spec.is_solana:
        buy_pred = f"{spec.amount_expr} > 0"
        buyer_col = spec.to_col  # owner (delta > 0)
    else:
        buy_pred = "1"
        buyer_col = spec.to_col

    # Snipers: distinct buyers inside the launch window.
    sniper_rows = client.execute(
        f"""
        SELECT countDistinct({buyer_col}), min({spec.block_col})
        FROM {spec.transfers_table}
        WHERE {spec.addr_col} = %(token)s
          AND {spec.block_col} BETWEEN %(lo)s AND %(hi)s
          AND {buy_pred}
          {chain_frag}
        """,
        params,
        settings={"max_execution_time": _QUERY_TIMEOUT_S},
    )
    sniper_count = int(sniper_rows[0][0] or 0) if sniper_rows else 0
    first_buy_block = int(sniper_rows[0][1]) if sniper_rows and sniper_rows[0][1] else None

    # Denominator: total distinct buyers ever (bounded — recent window only
    # for large tokens to protect the ClickHouse server timeout).
    total_rows = client.execute(
        f"""
        SELECT countDistinct({buyer_col})
        FROM (
            SELECT {buyer_col}, {spec.block_col}
            FROM {spec.transfers_table}
            WHERE {spec.addr_col} = %(token)s
              AND {buy_pred}
              {chain_frag}
            ORDER BY {spec.block_col} ASC
            LIMIT {_LARGE_TOKEN_RECENT_TRANSFERS}
        )
        """,
        params,
        settings={"max_execution_time": _QUERY_TIMEOUT_S},
    )
    total_buyers = int(total_rows[0][0] or 0) if total_rows else 0

    sniper_pct: float | None = None
    if total_buyers > 0:
        sniper_pct = round(sniper_count / total_buyers * 100, 2)

    return {
        "sniper_count": sniper_count,
        "sniper_pct": sniper_pct,
        "first_buy_block": first_buy_block,
        "launch_block": launch_block,
        "total_buyers": total_buyers,
    }


async def detect_snipers(
    token_address: str,
    chain: str,
    launch_block: int | None = None,
) -> dict[str, Any]:
    """Detect launch snipers for a token (best-effort, never raises)

    Args:
        token_address: Token contract / mint address.
        chain: User-facing chain name.
        launch_block: Known launch block/slot (from the scan's launch data).
            When None, resolved from ``deployer_events`` / min-block fallback.

    Returns::

        {"result": "scanned", "sniper_count": int, "sniper_pct": float | None,
         "first_buy_block": int | None}
        # or
        {"result": "unavailable", "note": "no launch block"}
        # or
        {"result": "error", "error": "..."}
    """
    try:
        client = _new_ch_client()
        block = launch_block
        if block is None:
            block = await run_sync(_resolve_launch_block, token_address, chain, client)
        if not block:
            return {"result": "unavailable", "note": "no launch block"}
        res = await run_sync(_query_snipers, token_address, chain, int(block), client)
    except Exception as exc:
        logger.debug("sniper_query_failed", error=str(exc)[:200], chain=chain)
        return {"result": "error", "error": str(exc)[:200]}
    if res is None:
        return {"result": "unavailable", "note": "clickhouse unavailable"}
    return {"result": "scanned", **res}
