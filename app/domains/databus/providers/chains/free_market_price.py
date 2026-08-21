"""
Free market/price provider tiers (chain: ``market_price_free``).

Split out of ``free_security.py`` (degodding phase 3, 2026-08-21).
Each provider returns ``dict | None`` and follows the DataBus provider
contract: return ``None`` to fall through to the next tier in the chain.

Tier order (registered in ``registry/categories/market.py``):
  1. ``_free_market_clickhouse_twap`` — own ClickHouse DEX TWAP (LOCAL, zero cost)
  3. ``_free_market_coingecko``       — CoinGecko simple/token_price (free, no key)
  4. ``_free_market_geckoterminal``   — GeckoTerminal token price (free, no auth)
  5a. ``_free_market_paraswap_quote`` — ParaSwap price quote (free, no auth)
  5b. ``_free_market_oneinch_quote``  — 1inch quote (FREEMIUM — requires API key)

The legacy ``chains/free_apis.py::_coingecko_price`` and the scanner's
``engines/_price_adapters.py`` delegate to these implementations via the
``free_security`` re-export hub.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.domains.databus.providers.chains.free_security_common import (
    COINGECKO_PLATFORMS,
    EVM_CHAIN_IDS,
    GECKOTERMINAL_NETWORKS,
    _normalize_chain,
)

logger = get_logger("databus.providers.free_security")


async def _free_market_clickhouse_twap(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 1 — own ClickHouse DEX events TWAP (LOCAL, zero cost).

    Wraps the scanner's own_price_oracle which computes TWAP from
    chain-indexer dex_events. Only returns when the oracle answered
    from own data (source == 'own').
    """
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", ""))).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    if not token_address or not chain:
        return None
    try:
        from app.domains.scanner.engines.own_price_oracle import get_token_price

        result = await get_token_price(token_address, chain)
        if result and result.get("source") == "own":
            result["tier"] = "local"
            result["is_local"] = True
            result["provider_source"] = "clickhouse_twap"
            return result
        logger.debug(
            "own TWAP insufficient for %s (source: %s)",
            token_address[:16],
            result.get("source") if result else "unavailable",
        )
    except ImportError:
        logger.debug("own_price_oracle not available")
    except Exception as exc:
        logger.debug("own_price_oracle failed: %s", str(exc)[:80])
    return None


async def _free_market_coingecko(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 3 — CoinGecko simple/token_price (free, 10-30 req/min, no key)."""
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", ""))).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    platform = COINGECKO_PLATFORMS.get(chain)
    if not token_address or not platform:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.coingecko.com/api/v3/simple/token_price/{platform}",
                params={
                    "contract_addresses": token_address,
                    "vs_currencies": "usd",
                    "include_market_cap": "true",
                    "include_24hr_vol": "true",
                    "include_24hr_change": "true",
                },
            )
            if resp.status_code != 200:
                logger.debug("coingecko_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None
            token_data = data.get(token_address)
            if not isinstance(token_data, dict) or "usd" not in token_data:
                return None

            return {
                "price_usd": float(token_data.get("usd", 0) or 0),
                "market_cap_usd": token_data.get("usd_market_cap"),
                "volume_24h_usd": token_data.get("usd_24h_vol"),
                "price_change_24h_pct": token_data.get("usd_24h_change"),
                "source": "coingecko",
                "tier": "free_api",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("coingecko_free_failed", chain=chain)
    return None


async def _free_market_geckoterminal(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 4 — GeckoTerminal token price (free, no auth).

    https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{address}
    """
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", "")))
    chain = _normalize_chain(kwargs.get("chain", ""))
    network = GECKOTERMINAL_NETWORKS.get(chain)
    if not token_address or not network:
        return None
    # Solana base58 addresses are case-sensitive — preserve as-is for solana.
    token_address = str(token_address).strip() if chain == "solana" else str(token_address).lower()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{token_address}",
            )
            if resp.status_code != 200:
                logger.debug("geckoterminal_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            token_data = (data.get("data") or {}).get("attributes") if isinstance(data, dict) else None
            if not isinstance(token_data, dict) or not token_data.get("price_usd"):
                return None

            return {
                "price_usd": float(token_data.get("price_usd", 0) or 0),
                "fdv_usd": token_data.get("fdv_usd"),
                "total_supply": token_data.get("total_supply"),
                "token_name": token_data.get("name", ""),
                "token_symbol": token_data.get("symbol", ""),
                "source": "geckoterminal",
                "tier": "free_api",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("geckoterminal_failed", chain=chain)
    return None


async def _free_market_paraswap_quote(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 5a — ParaSwap price quote (free, no auth).

    https://apiv5.paraswap.io/prices (verified HTTP 200 without key).
    Returns the destAmount implied by the best route for 1 src unit.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", ""))).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    network_id = EVM_CHAIN_IDS.get(chain)
    if not token_address or network_id is None:
        return None
    src_token = kwargs.get("src_token", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")  # native ETH
    src_decimals = int(kwargs.get("src_decimals", 18))
    amount = kwargs.get("amount", 10**src_decimals)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://apiv5.paraswap.io/prices",
                params={
                    "srcToken": src_token,
                    "destToken": token_address,
                    "amount": amount,
                    "srcDecimals": src_decimals,
                    "destDecimals": int(kwargs.get("dest_decimals", 18)),
                    "side": "SELL",
                    "network": network_id,
                },
            )
            if resp.status_code != 200:
                logger.debug("paraswap_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            price_route = data.get("priceRoute") if isinstance(data, dict) else None
            if not isinstance(price_route, dict):
                return None

            src_amount = int(price_route.get("srcAmount", 0) or 0)
            dest_amount = int(price_route.get("destAmount", 0) or 0)
            if src_amount <= 0 or dest_amount <= 0:
                return None

            price = dest_amount / src_amount
            return {
                "price_usd": price,
                "src_amount": src_amount,
                "dest_amount": dest_amount,
                "src_token": price_route.get("srcToken", src_token),
                "dest_token": price_route.get("destToken", token_address),
                "best_route_exchanges": [
                    swap.get("swapExchanges", [{}])[0].get("exchange", "")
                    for swap in price_route.get("bestRoute", [])
                    if isinstance(swap, dict) and swap.get("swapExchanges")
                ],
                "source": "paraswap",
                "tier": "free_api",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("paraswap_failed", chain=chain)
    return None


async def _free_market_oneinch_quote(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 5b — 1inch quote (FREEMIUM — now requires API key, verified 401 without).

    https://api.1inch.dev/swap/v6.0/{chain_id}/quote
    """
    token_address = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", ""))).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    chain_id = EVM_CHAIN_IDS.get(chain)
    api_key = kwargs.get("api_key", "")
    if not token_address or chain_id is None or not api_key:
        return None
    src_token = kwargs.get("src_token", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")
    amount = kwargs.get("amount", 10**18)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.1inch.dev/swap/v6.0/{chain_id}/quote",
                params={
                    "src": src_token,
                    "dst": token_address,
                    "amount": amount,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code != 200:
                logger.debug("oneinch_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            if not isinstance(data, dict) or "toAmount" not in data:
                return None

            to_amount = int(data.get("toAmount", 0) or 0)
            if to_amount <= 0:
                return None
            price = to_amount / amount
            return {
                "price_usd": price,
                "src_amount": amount,
                "dest_amount": to_amount,
                "src_token": src_token,
                "dest_token": token_address,
                "source": "1inch",
                "tier": "freemium",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("oneinch_failed", chain=chain)
    return None
