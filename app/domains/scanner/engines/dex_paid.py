"""DEX-paid status — is this token listed/traded on a DEX?

Phanes-parity signal (2026-09-02): "DEX paid" means the token has at least
one swap on a decentralized exchange — i.e. it is actually tradable, not
just a fresh contract with no market. Computed from OWN ClickHouse data:
``wallet_memory.dex_events`` (same table for all chains, filtered by
``chain =``). A token is "paid" when any swap row references it as
``token0`` or ``token1``.

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


def _query_dex_paid(token_address: str, chain: str, client: Any) -> dict[str, Any] | None:
    """Sync ClickHouse query: DEX presence for one token."""
    if client is None:
        return None
    spec = get_chain_spec(chain)
    token = normalize_addr(token_address, chain)
    params: dict[str, Any] = {"token": token}
    if not spec.is_solana:
        params["chain"] = spec.chain
    rows = client.execute(
        f"""
        SELECT count(), groupUniqArray(dex)
        FROM {spec.dex_table}
        WHERE (token0 = %(token)s OR token1 = %(token)s)
          {spec.chain_filter}
        """,
        params,
        settings={"max_execution_time": _QUERY_TIMEOUT_S},
    )
    if not rows:
        return {"dex_paid": False, "dex_count": 0, "dexes": []}
    count, dexes = rows[0]
    return {
        "dex_paid": int(count or 0) > 0,
        "dex_count": int(count or 0),
        "dexes": sorted(str(d) for d in (dexes or []) if d),
    }


async def check_dex_paid(token_address: str, chain: str) -> dict[str, Any]:
    """Is this token listed/traded on a DEX? (best-effort, never raises)

    Returns::

        {"result": "scanned", "dex_paid": bool, "dex_count": int,
         "dexes": ["uniswap_v3", ...]}
        # or
        {"result": "error", "error": "..."}
    """
    try:
        res = await run_sync(_query_dex_paid, token_address, chain, _new_ch_client())
    except Exception as exc:
        logger.debug("dex_paid_query_failed", error=str(exc)[:200], chain=chain)
        return {"result": "error", "error": str(exc)[:200]}
    if res is None:
        return {"result": "unavailable", "note": "clickhouse unavailable"}
    return {"result": "scanned", **res}
