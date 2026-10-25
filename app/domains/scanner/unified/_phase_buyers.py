"""First-buyers analysis phase (own indexer data, best-effort).

Split out of ``_phase_helpers.py`` (debt wave, 2026-10-25).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.unified._phase_common import (
    _record_module_error,
    _record_module_unavailable,
)

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)

#: First-buyers sample size per scan tier (2026-08-29 depth push — synced
#: with the private scanner's FIRST_BUYERS_LIMIT_DEEP): quick/basic scans
#: surface the top 25 earliest buyers; deep/elite scans get the full
#: 250-wallet breakdown (raised from 200 — deep scans go deeper).
FIRST_BUYERS_LIMIT_QUICK = 25
FIRST_BUYERS_LIMIT_DEEP = 250


async def run_first_buyers(r: ScanResult, address: str, chain: str, tier: str) -> None:
    """Run first-buyers analysis from own indexer data (best-effort).

    Ranks the earliest distinct buyers and classifies each wallet (fresh /
    new / established age buckets, same-window bundling) — see
    ``engines/first_buyers.py``. Sample size scales with tier: 25 on
    quick scans, 250 on deep/elite. Never raises; a ClickHouse outage or
    unindexed token leaves ``r.first_buyers`` as None.
    """
    try:
        from app.domains.scanner.engines.first_buyers import get_first_buyers

        limit = FIRST_BUYERS_LIMIT_DEEP if (tier or "").lower() in ("elite", "admin") else FIRST_BUYERS_LIMIT_QUICK
        result = await get_first_buyers(address, chain, limit=limit)
        if isinstance(result, dict) and result.get("buyers"):
            r.first_buyers = result
            r.modules_run.append("first_buyers")
            r.enrichment_sources.append("first_buyers:own")
        else:
            _record_module_unavailable(
                r,
                "first_buyers",
                chain,
                str((result or {}).get("reason", "")) if isinstance(result, dict) else "no buyer data",
            )
    except Exception as exc:
        logger.warning("first_buyers_failed", error=str(exc)[:200], chain=chain, token=address[:16])
        _record_module_error(r, "first_buyers", chain, str(exc))
