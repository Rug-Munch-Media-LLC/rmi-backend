"""Wallet-phase inputs — indexed-transfer probe and holder resolution.

Split out of ``_phase_helpers.py`` (debt wave, 2026-10-25): both are input
plumbing for the wallet engines (cheap "is this token indexed?" probe, and
the DataBus-payload → own-ClickHouse holder fallback), not orchestration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.unified._phase_common import _record_module_unavailable

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)

#: Lookback window for the cheap "is this token indexed at all?" probe.
TRANSFER_PROBE_DAYS = 7
#: Top-holder depth requested from the engines (funding + freshness sample).
HOLDER_SAMPLE = 10
#: Freshness sample size — each wallet costs two heavy ClickHouse queries.
FRESHNESS_HOLDER_SAMPLE = 5

_TRANSFER_COUNT_SQL = """
SELECT count()
FROM wallet_memory.evm_transfers
WHERE chain = %(chain)s
  AND token_address = %(token)s
  AND block_timestamp >= now() - INTERVAL 7 DAY
"""


def _count_recent_transfers(ch_client: Any, addr: str, ch: str) -> int | None:
    """Count 7-day transfers for one token; ``None`` when the probe cannot run."""
    client = ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(_TRANSFER_COUNT_SQL, {"chain": ch, "token": addr}, settings={"max_execution_time": 5})
        return int(rows[0][0] or 0) if rows else 0
    except (ValueError, RuntimeError, OSError):
        return None


async def probe_recent_transfers(r: ScanResult, address: str, chain: str) -> dict[str, Any] | None:
    """Early-exit payload when the token has no indexed transfers in 7 days.

    There is no skip index on ``evm_transfers.token_address``, so every
    token-filtered query scans the full window (~110M rows/7d on base). For a
    token with ZERO indexed transfers each of the ~8 heavy queries below burns
    its 10-15s cap finding nothing and the sequential engines blow the 120s
    phase budget before any module completes. A bounded 7-day count (~1.2s on
    base, verified) detects that cheaply: 0 rows → the engines cannot produce
    anything → record honest "unavailable" notes and return.

    ``None`` (ClickHouse unreachable / probe timeout) fails open: the normal
    path proceeds.
    """
    if chain == "solana":
        return None
    try:
        from app.domains.scanner.engines._common import run_sync
        from app.domains.scanner.engines._indexer_port import ch_client

        if await run_sync(_count_recent_transfers, ch_client, address, chain) != 0:
            return None
        note = "no indexed transfers in last 7 days — token may be brand new or unindexed"
        logger.info("wallet_analysis_skipped_no_data", chain=chain, token=address[:16])
        for module in ("funding_analysis", "wallet_freshness", "holder_overlap", "wallet_metrics"):
            _record_module_unavailable(r, module, chain, note)
        return {"result": "unavailable", "note": note}
    except Exception as exc:  # pragma: no cover — pre-check must never break the phase
        logger.debug("wallet_analysis_precheck_failed", error=str(exc)[:120])
        return None


def holders_from_payload(holder_data: Any) -> list[str]:
    """Extract holder addresses from the many DataBus payload shapes."""
    if not isinstance(holder_data, dict):
        return []
    top = holder_data.get("top_holders") or []
    if isinstance(top, list):
        holders = [str(h.get("address", "")) for h in top if isinstance(h, dict) and h.get("address")]
    elif isinstance(top, dict):
        # Some providers return {address: pct}
        holders = [str(a) for a in top if a]
    else:
        holders = []
    if not holders and isinstance(holder_data.get("holders"), list):
        # Birdeye/GMGN-style payloads: [{"address": ...}, ...]
        holders = [str(h.get("address", "")) if isinstance(h, dict) else str(h) for h in holder_data["holders"] if h]
    return holders


async def resolve_holders(r: ScanResult, address: str, chain: str) -> list[str]:
    """Top holders from the scan payload, falling back to own ClickHouse.

    The DataBus ``holder_data`` chain does not always carry ``top_holders``
    (e.g. the sentinelscan cache path returns a full scan payload without
    one). Without holders the freshness phase had ZERO per-wallet tasks and
    degraded to "no freshness data" on every orchestrated scan while direct
    calls (which fetch their own holders) worked, so fall back to the engine's
    own top-holders query — same source ``compute_wallet_metrics`` uses.
    """
    holders = holders_from_payload(r.holders)
    if holders:
        return holders
    try:
        from app.domains.scanner.engines._common import run_sync
        from app.domains.scanner.engines._funding_queries import _query_top_holders

        rows = await run_sync(_query_top_holders, address, chain, HOLDER_SAMPLE)
        holders = [str(row[0]) for row in (rows or []) if row and row[0]]
        if holders:
            logger.info("wallet_analysis_holders_fallback", chain=chain, count=len(holders))
    except Exception as exc:  # pragma: no cover — best-effort fallback
        logger.debug("wallet_analysis_holders_fallback_failed", error=str(exc)[:120])
    return holders
