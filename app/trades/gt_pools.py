"""GeckoTerminal pool discovery.

A token can trade in many pools; trades live on the POOL, not the token. This
module resolves the deepest pool for a (chain, token) pair and returns the
pool's base token, which is needed to interpret ``kind`` as buy/sell.
"""

from __future__ import annotations

from typing import NamedTuple

from app.core.logging import get_logger
from app.trades.http import get_json
from app.trades.schema import canon_chain

logger = get_logger(__name__)

GT_BASE = "https://api.geckoterminal.com/api/v2"


class PoolRef(NamedTuple):
    pool: str
    base_token: str
    quote_token: str
    dex: str


async def resolve_pool(chain: str, token: str) -> PoolRef | None:
    """Return the leading pool for a token, or None when GeckoTerminal has none."""
    network = canon_chain(chain)
    data = await get_json(
        f"{GT_BASE}/networks/{network}/tokens/{token}/pools",
        source="geckoterminal.pools",
    )
    if not isinstance(data, dict):
        return None
    rows = data.get("data") or []
    if not rows:
        return None

    best = rows[0]
    for row in rows:
        try:
            if float(row["attributes"].get("reserve_in_usd") or 0) > float(
                best["attributes"].get("reserve_in_usd") or 0
            ):
                best = row
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug("gt_pool_reserve_unreadable", pool=str(row.get("id")), error=type(exc).__name__)
            continue

    attrs = best.get("attributes") or {}
    rels = best.get("relationships") or {}
    return PoolRef(
        pool=attrs.get("address") or _strip_network(best.get("id")),
        base_token=_rel_addr(rels, "base_token"),
        quote_token=_rel_addr(rels, "quote_token"),
        dex=attrs.get("dex_id") or "",
    )


def _rel_addr(rels: dict, key: str) -> str:
    try:
        return (rels[key]["data"]["id"] or "").split("_", 1)[1]
    except (KeyError, IndexError, TypeError) as exc:
        logger.debug("gt_pool_relationship_missing", key=key, error=type(exc).__name__)
        return ""


def _strip_network(pool_id: str | None) -> str:
    if not pool_id:
        return ""
    return pool_id.split("_", 1)[1] if "_" in pool_id else pool_id
