"""
Price Oracle DataBus Providers — LOCAL-tier price & token-meta providers
========================================================================
Exposes the F2 price/decimals oracle (app/domains/scanner/engines/price_oracle.py)
through the DataBus as LOCAL-tier providers. These are preferred first in the
fallback chains because they are free (own ClickHouse dex_events + verified
free APIs) and Redis/ClickHouse-cached.

  DataBus fetch("token_price", {token_address, chain}) → token_price_local
  DataBus fetch("token_meta",  {token_address, chain}) → token_meta_local

Every function returns dict | None following the DataBus provider contract:
return None to fall through to the next provider in the chain.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.providers.price_oracle")


async def token_price_local(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — USD price via the F2 price oracle.

    Parameters (from DataBus fetch kwargs):
      token_address: str — token contract address (also accepts token/mint/address)
      chain: str — chain name
      force_fresh: bool — bypass caches (optional)

    Returns dict with the oracle payload (price_usd, source, timestamp,
    confidence) or None when every tier fails.
    """
    token = kwargs.get("token_address", kwargs.get("address", kwargs.get("token", kwargs.get("mint", ""))))
    chain = kwargs.get("chain", "")
    force_fresh = bool(kwargs.get("force_fresh", False))

    if not token or not chain:
        return None

    try:
        from app.domains.scanner.engines.price_oracle import get_token_price

        result = await get_token_price(str(token), str(chain), force_fresh=force_fresh)
        if not result or float(result.get("price_usd", 0) or 0) <= 0:
            return None

        return {
            "data": result,
            "source": f"price_oracle ({result.get('source', 'unknown')})",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("token_price_local failed: %s", exc)
        return None


async def token_meta_local(**kwargs: Any) -> dict[str, Any] | None:
    """LOCAL provider — token metadata (decimals, symbol, name, last price).

    Parameters (from DataBus fetch kwargs):
      token_address: str — token contract address (also accepts token/mint/address)
      chain: str — chain name

    Returns dict with token metadata + last known price, or None when the
    token cannot be resolved.
    """
    token = kwargs.get("token_address", kwargs.get("address", kwargs.get("token", kwargs.get("mint", ""))))
    chain = kwargs.get("chain", "")

    if not token or not chain:
        return None

    try:
        from app.domains.scanner.engines.price_oracle import (
            get_token_decimals,
            get_token_price,
        )

        decimals = await get_token_decimals(str(token), str(chain))
        price = await get_token_price(str(token), str(chain))

        meta: dict[str, Any] = {
            "token_address": str(token),
            "chain": str(chain),
            "decimals": decimals,
        }
        if price:
            meta["price_usd"] = float(price.get("price_usd", 0) or 0)
            meta["price_source"] = price.get("source", "")
            meta["confidence"] = float(price.get("confidence", 0) or 0)
            if price.get("symbol"):
                meta["symbol"] = price["symbol"]
            if price.get("name"):
                meta["name"] = price["name"]

        if decimals is None and not price:
            return None

        return {
            "data": meta,
            "source": "price_oracle (token_meta)",
            "tier": "local",
            "is_local": True,
        }
    except Exception as exc:
        logger.debug("token_meta_local failed: %s", exc)
        return None
