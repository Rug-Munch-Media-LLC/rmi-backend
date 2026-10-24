"""Live DexScreener token boosts — the only sanctioned 'paid attention' feed.

Boosted tokens are *excluded from rank* by design: a boost is bought
visibility, not measured on-chain demand.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("trending.boosts")

_BOOST_URLS = (
    "https://api.dexscreener.com/token-boosts/top/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
)


def _norm(chain_id: str) -> str:
    return {"eth": "ethereum", "sol": "solana"}.get(chain_id.lower(), chain_id.lower())


async def fetch_boosts(chain: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Map ``(chain, token_address)`` -> boost metadata (amounts + icon)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=5.0)) as client:
            for url in _BOOST_URLS:
                try:
                    resp = await client.get(url)
                except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
                    logger.debug("boost fetch failed %s: %s", url, exc)
                    continue
                if resp.status_code != 200:
                    continue
                payload = resp.json()
                items = payload if isinstance(payload, list) else payload.get("tokens", [])
                for item in items if isinstance(items, list) else []:
                    item_chain = _norm(str(item.get("chainId") or ""))
                    addr = str(item.get("tokenAddress") or "")
                    if not addr or (chain and item_chain != chain):
                        continue
                    key = (item_chain, addr)
                    prev = out.get(key) or {}
                    out[key] = {
                        "amount": item.get("amount", prev.get("amount")),
                        "total_amount": item.get("totalAmount", prev.get("total_amount")),
                        "icon": item.get("icon") or prev.get("icon"),
                        "description": item.get("description") or prev.get("description"),
                        "url": item.get("url") or prev.get("url"),
                        "sources": sorted({*(prev.get("sources") or []), url}),
                    }
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError) as exc:
        logger.debug("boosts unavailable: %s", exc)
    return out
