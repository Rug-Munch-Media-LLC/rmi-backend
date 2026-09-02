"""Dev-sold detection — has the deployer sold tokens of this launch?

Phanes-parity signal (2026-09-02): "dev sold" is one of the highest-signal
rug indicators on new launches. We compute it from OWN ClickHouse data:

  * EVM     — ``wallet_memory.evm_transfers``: a sell is a transfer row with
              ``from_address = deployer`` and ``token_address = token``.
  * Solana  — ``wallet_memory.solana_spl_balance_changes``: a signed
              balance row with ``owner = deployer``, ``mint = token`` and
              ``delta < 0``.

``total_sold_pct`` is the share of the tokens the deployer ever ACQUIRED
(bought + received) that it has since sold: ``sold / (sold + received)``.
This is deliberately supply-agnostic — the indexer tables carry no reliable
total supply, and "of what the dev held, how much left" is the actionable
question.

Fail-open by contract: unknown deployer → ``{"result": "unavailable"}``;
ClickHouse outage → ``{"result": "error"}``. Never raises.
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines._chain_data import get_chain_spec, normalize_addr
from app.domains.scanner.engines._common import _norm_addr, run_sync, to_iso
from app.domains.scanner.engines._indexer_port import new_ch_client as _new_ch_client
from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)

_QUERY_TIMEOUT_S = 10


def _query_dev_sells(
    token_address: str,
    chain: str,
    deployer_address: str,
    client: Any,
) -> dict[str, Any] | None:
    """Sync ClickHouse query: deployer sell/buy aggregates for one token."""
    if client is None:
        return None
    spec = get_chain_spec(chain)
    token = normalize_addr(token_address, chain)
    deployer = _norm_addr(deployer_address, chain)
    params: dict[str, Any] = {"token": token, "deployer": deployer}

    if spec.is_solana:
        # Signed balance deltas: sell = delta < 0, buy/receive = delta > 0.
        query = f"""
            SELECT countIf({spec.amount_expr} < 0),
                   maxIf({spec.ts_col}, {spec.amount_expr} < 0),
                   sumIf(-{spec.amount_expr}, {spec.amount_expr} < 0) AS sold,
                   sumIf({spec.amount_expr}, {spec.amount_expr} > 0) AS received
            FROM {spec.transfers_table}
            WHERE {spec.addr_col} = %(token)s
              AND {spec.from_col} = %(deployer)s
        """
    else:
        # Transfers: sell = FROM deployer, buy/receive = TO deployer.
        query = f"""
            SELECT countIf({spec.from_col} = %(deployer)s),
                   maxIf({spec.ts_col}, {spec.from_col} = %(deployer)s),
                   sumIf({spec.amount_expr}, {spec.from_col} = %(deployer)s) AS sold,
                   sumIf({spec.amount_expr}, {spec.to_col} = %(deployer)s) AS received
            FROM {spec.transfers_table}
            WHERE {spec.addr_col} = %(token)s
              AND ({spec.from_col} = %(deployer)s OR {spec.to_col} = %(deployer)s)
              {spec.chain_filter}
        """
        params["chain"] = spec.chain

    rows = client.execute(query, params, settings={"max_execution_time": _QUERY_TIMEOUT_S})
    if not rows:
        return {"sell_count": 0, "last_sell_ts": None, "sold": 0.0, "received": 0.0}
    count, last_ts, sold, received = rows[0]
    return {
        "sell_count": int(count or 0),
        "last_sell_ts": to_iso(last_ts),
        "sold": float(sold or 0.0),
        "received": float(received or 0.0),
    }


async def check_dev_sold(token_address: str, chain: str, deployer_address: str | None = None) -> dict[str, Any]:
    """Has the deployer sold tokens of this launch? (best-effort, never raises)

    Returns::

        {"result": "scanned", "dev_sold": bool, "sell_count": int,
         "last_sell_ts": str | None, "total_sold_pct": float | None}
        # or
        {"result": "unavailable", "note": "no deployer address"}
        # or
        {"result": "error", "error": "..."}
    """
    if not deployer_address:
        return {"result": "unavailable", "note": "no deployer address"}
    try:
        res = await run_sync(_query_dev_sells, token_address, chain, deployer_address, _new_ch_client())
    except Exception as exc:
        logger.debug("dev_sold_query_failed", error=str(exc)[:200], chain=chain)
        return {"result": "error", "error": str(exc)[:200]}
    if res is None:
        return {"result": "unavailable", "note": "clickhouse unavailable"}

    sold = res.get("sold") or 0.0
    received = res.get("received") or 0.0
    total_sold_pct: float | None = None
    if sold > 0 and (sold + received) > 0:
        total_sold_pct = round(sold / (sold + received) * 100, 2)

    return {
        "result": "scanned",
        "dev_sold": bool(res.get("sell_count")),
        "sell_count": res.get("sell_count", 0),
        "last_sell_ts": res.get("last_sell_ts"),
        "total_sold_pct": total_sold_pct,
    }
