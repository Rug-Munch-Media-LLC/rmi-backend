"""GMGN recent-trades adapter.

Endpoint: ``GET https://gmgn.ai/defi/quotation/v1/trades/{chain}/{mint}``

Reality check (verified live 2026-10-24 from Talos): the endpoint answers
Cloudflare's "Attention Required" challenge to datacenter IPs, so GMGN is
DISABLED unless ``GMGN_ENABLED=1`` and a Cloudflare-capable egress (the fleet
FlareSolverr) is wired in front of it. It is kept as an adapter, never as the
primary, and it never fabricates a row.
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.trades.http import get_json
from app.trades.numeric import as_number, epoch_ms
from app.trades.schema import Trade, canon_chain

logger = get_logger(__name__)

GMGN_BASE = "https://gmgn.ai/defi/quotation/v1"
_HEADERS = {"referer": "https://gmgn.ai/?chain=sol"}


def enabled() -> bool:
    return os.getenv("GMGN_ENABLED", "").strip() in ("1", "true", "True")


async def fetch_gmgn_trades(*, chain: str, token: str, limit: int = 100) -> list[Trade]:
    """Return GMGN trades, or [] when disabled/blocked. Never synthesized."""
    if not enabled():
        return []
    network = canon_chain(chain)
    data: Any = await get_json(
        f"{GMGN_BASE}/trades/{network}/{token}",
        params={"limit": min(limit, 500)},
        headers=_HEADERS,
        source="gmgn",
    )
    if not isinstance(data, dict):
        return []

    rows = (data.get("data") or {}).get("history") or []
    trades: list[Trade] = []
    for t in rows:
        tx = t.get("tx_hash") or ""
        if not tx:
            continue
        event = str(t.get("event") or "").lower()
        if event not in ("buy", "sell"):
            continue
        trades.append(
            Trade(
                chain=network,
                token=token,
                tx=tx,
                side=event,
                amount_token=as_number(t.get("amount")),
                amount_usd=as_number(t.get("total")),
                price_usd=as_number(t.get("price")),
                wallet=t.get("maker") or "",
                maker=t.get("maker") or "",
                timestamp=str(t.get("timestamp") or ""),
                timestamp_ms=epoch_ms(t.get("timestamp")),
                pool="",
                source="gmgn",
            )
        )
    return trades[:limit]

