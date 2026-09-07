"""Whale / smart-money flow + signal API router (read-only).

Thin router over the existing whale-flow enrichment modules — no
re-implemented logic, just composition + graceful degradation:

  GET /api/v1/whales/flow     — whale/smart-money flow for a token
  GET /api/v1/whales/signals  — rendered whale signal for a token

Both handlers reuse ``whale_flow`` / ``whale_signal`` from
``app.domains.scanner.whale_flow_enrich`` — the same fail-open fetchers the
scanner uses (ClickHouse whale transfers + GMGN smart-money direction). No
network/DB work happens at import time; the fetchers lazy-import their
providers inside the function bodies and degrade to ``None`` fields on any
error (never raise).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency
from app.domains.scanner.whale_flow_enrich import whale_flow, whale_signal

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/whales", tags=["whales"])


@router.get(
    "/flow",
    summary="Whale / Smart-Money Flow for a Token",
    dependencies=[Depends(get_rate_limit_dependency("whales1", rate=2.0, burst=5))],
)
async def flow(
    address: str = Query(..., description="Token contract address"),
    chain: str = Query(..., description="Chain (e.g. solana, ethereum)"),
) -> dict[str, Any]:
    """Whale / smart-money flow signals for a token (fail-open).

    Returns ``net_flow`` (whale buy-sell net USD, sign = direction),
    ``whale_tx_count``, ``smart_money_bought`` / ``smart_money_sold`` and a
    human-readable ``evidence`` summary. Every field is ``None`` when the
    upstream source is unavailable — never raises.
    """
    try:
        result = await whale_flow(address, chain)
        return {
            "address": address,
            "chain": chain,
            "net_flow": result.get("net_flow"),
            "whale_tx_count": result.get("whale_tx_count"),
            "smart_money_bought": result.get("smart_money_bought"),
            "smart_money_sold": result.get("smart_money_sold"),
            "evidence": result.get("evidence"),
            "source": "whale_flow_enrich",
        }
    except Exception as e:  # graceful degradation contract
        logger.warning("whales_flow_failed: %s", str(e)[:120])
        return {
            "address": address,
            "chain": chain,
            "net_flow": None,
            "whale_tx_count": None,
            "smart_money_bought": None,
            "smart_money_sold": None,
            "evidence": None,
            "source": "whale_flow_enrich",
        }


@router.get(
    "/signals",
    summary="Rendered Whale Signal for a Token",
    dependencies=[Depends(get_rate_limit_dependency("whales2", rate=2.0, burst=5))],
)
async def signals(
    address: str = Query(..., description="Token contract address"),
    chain: str = Query(..., description="Chain (e.g. solana, ethereum)"),
) -> dict[str, Any]:
    """Rendered whale signal for a token (fail-open).

    Composes the whale flow and renders it through ``whale_signal`` into a
    human-readable flag (``"whales net selling"``, ``"smart money bought
    in"``, ``"smart money exiting"``) or ``None`` when there is no signal.
    Never raises.
    """
    try:
        result = await whale_flow(address, chain)
        return {
            "address": address,
            "chain": chain,
            "signal": whale_signal(result),
            "flow": result,
            "source": "whale_flow_enrich",
        }
    except Exception as e:  # graceful degradation contract
        logger.warning("whales_signals_failed: %s", str(e)[:120])
        return {
            "address": address,
            "chain": chain,
            "signal": None,
            "flow": None,
            "source": "whale_flow_enrich",
        }
