"""Price metrics — ATH, current price and 1h change from OWN DEX data.

Phanes-parity signals (2026-09-02): all-time high (ATH), current price and
1-hour price change, computed from ``wallet_memory.dex_events`` swap rows
(same table for all chains, filtered by ``chain =``):

  * ``ath``            — ``max(price_usd)`` over all swaps of the token.
  * ``current_price``  — ``argMax(price_usd, block_timestamp)`` (latest swap).
  * ``change_1h_pct``  — latest price vs the last swap price at/after
                         ``now() - 1h`` (both sides from dex_events, so the
                         comparison is apples-to-apples).
  * ``ath_ts``         — timestamp of the ATH swap (argMax over price).

Fail-open by contract: no DEX rows → ``{"result": "unavailable"}``;
ClickHouse outage → ``{"result": "error"}``. Never raises.
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines._chain_data import get_chain_spec, normalize_addr
from app.domains.scanner.engines._common import run_sync, to_iso
from app.domains.scanner.engines._indexer_port import new_ch_client as _new_ch_client
from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)

_QUERY_TIMEOUT_S = 10


def _query_price_metrics(token_address: str, chain: str, client: Any) -> dict[str, Any] | None:
    """Sync ClickHouse query: ATH / current / 1h-ago price for one token."""
    if client is None:
        return None
    spec = get_chain_spec(chain)
    token = normalize_addr(token_address, chain)
    params: dict[str, Any] = {"token": token}
    if not spec.is_solana:
        params["chain"] = spec.chain
    token_filter = f"(token0 = %(token)s OR token1 = %(token)s) {spec.chain_filter}"

    # ATH + current price in one pass over the token's swaps.
    rows = client.execute(
        f"""
        SELECT max(price_usd),
               argMax(price_usd, {spec.ts_col}),
               argMax({spec.ts_col}, price_usd)
        FROM {spec.dex_table}
        WHERE {token_filter}
          AND price_usd > 0
        """,
        params,
        settings={"max_execution_time": _QUERY_TIMEOUT_S},
    )
    if not rows or rows[0][0] is None:
        return None
    ath, current, _ath_ts = rows[0]
    ath_f = float(ath or 0.0)
    current_f = float(current or 0.0)

    # 1h-ago reference price: last swap at/after now()-1h.
    hour_rows = client.execute(
        f"""
        SELECT price_usd
        FROM {spec.dex_table}
        WHERE {token_filter}
          AND price_usd > 0
          AND {spec.ts_col} >= now() - INTERVAL 1 HOUR
        ORDER BY {spec.ts_col} DESC
        LIMIT 1
        """,
        params,
        settings={"max_execution_time": _QUERY_TIMEOUT_S},
    )
    hour_ago = float(hour_rows[0][0]) if hour_rows and hour_rows[0][0] else None

    change_1h_pct: float | None = None
    if hour_ago and hour_ago > 0 and current_f > 0:
        change_1h_pct = round((current_f - hour_ago) / hour_ago * 100, 2)

    return {
        "ath": ath_f,
        "current_price": current_f,
        "change_1h_pct": change_1h_pct,
        "ath_ts": to_iso(_ath_ts),
    }


async def get_price_metrics(token_address: str, chain: str) -> dict[str, Any]:
    """ATH / current price / 1h change for a token (best-effort, never raises)

    Returns::

        {"result": "scanned", "ath": float, "current_price": float,
         "change_1h_pct": float | None, "ath_ts": str | None}
        # or
        {"result": "unavailable", "note": "no DEX data"}
        # or
        {"result": "error", "error": "..."}
    """
    try:
        res = await run_sync(_query_price_metrics, token_address, chain, _new_ch_client())
    except Exception as exc:
        logger.debug("price_metrics_query_failed", error=str(exc)[:200], chain=chain)
        return {"result": "error", "error": str(exc)[:200]}
    if res is None:
        return {"result": "unavailable", "note": "no DEX data"}
    return {"result": "scanned", **res}
