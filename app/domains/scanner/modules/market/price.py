"""Price-related market checks — consensus, CoinGecko, 1inch."""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    GOPLUS_CHAIN_IDS,
    PREFER_OWN_DATA,
    _fetch_dexscreener,
    _fetch_json,
    logger,
)


async def _get_price_consensus(token_address: str, chain: str, market_price: float = 0) -> dict[str, Any] | None:
    """Get price consensus across multiple sources.

    When PREFER_OWN_DATA=true (default), own_price_oracle is queried FIRST
    as it computes TWAP/VWAP from ClickHouse dex_events (5-50ms, zero cost).
    Falls back to DexScreener and GoPlus API.
    """
    result: dict[str, Any] = {
        "check": "price",
        "result": "scanned",
        "price_usd": 0,
        "sources": [],
        "price_consensus": False,
    }

    prices: dict[str, float] = {}

    # ── Tier 1: Own price oracle (ClickHouse dex_events VWAP) ──
    if PREFER_OWN_DATA:
        try:
            from app.domains.scanner.engines.own_price_oracle import get_token_price

            own_price = await get_token_price(token_address, chain)
            price = own_price.get("price_usd", 0)
            if price > 0:
                prices["own_data"] = price
                result["price_usd"] = price
                result["volume_24h"] = own_price.get("volume_24h_usd", 0)
                result["price_change_24h"] = own_price.get("price_change_24h_pct", 0)
                result["confidence"] = own_price.get("confidence", 0)
                result["market_cap_usd"] = own_price.get("market_cap_usd")
                result["source"] = own_price.get("source", "own_data")
                logger.info(
                    "Using own price data for %s on %s (confidence: %s)",
                    token_address[:16],
                    chain,
                    own_price.get("confidence", 0),
                )
        except ImportError:
            logger.debug("own_price_oracle not available")
        except Exception as exc:
            logger.debug("own_price_oracle_failed", error=str(exc)[:60])

    # ── Tier 2: DexScreener (via _fetch_dexscreener which also tries own data first) ──
    try:
        data = await _fetch_dexscreener(token_address, chain)
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            price = float(pair.get("priceUsd", 0) or 0)
            if price > 0:
                prices["dexscreener"] = price
                if not prices.get("own_data"):
                    result["price_usd"] = price
                    result["volume_24h"] = float(pair.get("volume", {}).get("h24", 0) or 0)
                    result["liquidity_usd"] = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                    result["fdv"] = float(pair.get("fdv", 0) or 0)
                    result["price_change_24h"] = pair.get("priceChange", {}).get("h24", 0)
                    result["dex"] = pair.get("dexId", "")
                    result["pair_address"] = pair.get("pairAddress", "")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.debug("dexscreener_price_failed", error=str(e))

    # ── Tier 3: GoPlus (has price info for some tokens) ──
    try:
        chain_id = GOPLUS_CHAIN_IDS.get(chain, chain)
        goplus_data = await _fetch_json(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": token_address},
        )
        if goplus_data:
            goplus_result = goplus_data.get("result", {}).get(token_address.lower(), {})
            if goplus_result:
                prices["goplus"] = float(goplus_result.get("price", 0) or 0)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.debug("goplus_price_failed", error=str(e))

    # Determine consensus
    result["sources"] = list(prices.keys())
    if len(prices) >= 1:
        result["price_usd"] = max(prices.values())  # Use highest confidence
        result["price_consensus"] = True

    return result


async def _check_coingecko_price(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check price for token — own ClickHouse data first, then CoinGecko.

    When PREFER_OWN_DATA=true (default), queries own_price_oracle FIRST
    which computes TWAP/VWAP from ClickHouse dex_events (5-50ms, zero cost).
    Falls back to CoinGecko API only when our data has insufficient confidence
    or the token isn't tracked.
    """
    logger.info("check_coingecko", token=token_address, chain=chain)

    # ── Tier 1: Own price oracle (ClickHouse dex_events) ──
    if PREFER_OWN_DATA:
        try:
            from app.domains.scanner.engines.own_price_oracle import get_token_price

            own_price = await get_token_price(token_address, chain)
            if own_price.get("source") in ("own", "defillama") and own_price.get("price_usd", 0) > 0:
                confidence = own_price.get("confidence", 0)
                if confidence >= 0.3:
                    logger.info(
                        "Using own price data for %s on %s (confidence: %s)",
                        token_address[:16],
                        chain,
                        confidence,
                    )
                    return {
                        "check": "coingecko",
                        "result": "scanned",
                        "is_listed": True,
                        "price_usd": own_price["price_usd"],
                        "market_cap_usd": own_price.get("market_cap_usd"),
                        "volume_24h_usd": own_price.get("volume_24h_usd", 0),
                        "price_change_24h_pct": own_price.get("price_change_24h_pct", 0),
                        "name": "",
                        "symbol": "",
                        "source": own_price.get("source", "own_data"),
                    }
                logger.debug(
                    "Own price confidence too low (%s) for %s, falling back to CoinGecko",
                    confidence,
                    token_address[:16],
                )
        except ImportError:
            logger.debug("own_price_oracle not available")
        except Exception as exc:
            logger.debug("own_price_oracle_failed", error=str(exc)[:60])

    # ── Tier 2: CoinGecko API (original behavior) ──
    platform_map: dict[str, str] = {
        "ethereum": "ethereum",
        "eth": "ethereum",
        "bsc": "binance-smart-chain",
        "polygon": "polygon-pos",
        "arbitrum": "arbitrum-one",
        "optimism": "optimistic-ethereum",
        "base": "base",
        "avalanche": "avalanche",
        "fantom": "fantom",
    }
    platform = platform_map.get(chain)
    if not platform:
        return {
            "check": "coingecko",
            "result": "scanned",
            "is_listed": False,
            "note": f"Chain {chain} not on CoinGecko",
            "source": "unavailable",
        }

    try:
        url = f"https://api.coingecko.com/api/v3/coins/{platform}/contract/{token_address}"
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return {
                    "check": "coingecko",
                    "result": "scanned",
                    "is_listed": False,
                    "note": "Token not found on CoinGecko",
                    "source": "unavailable",
                }
            if resp.status_code != 200:
                return {
                    "check": "coingecko",
                    "result": "unavailable",
                    "error": f"HTTP {resp.status_code}",
                    "source": "unavailable",
                }

            data = resp.json()
            market_data = data.get("market_data", {})

            return {
                "check": "coingecko",
                "result": "scanned",
                "is_listed": True,
                "name": data.get("name", ""),
                "symbol": data.get("symbol", ""),
                "price_usd": market_data.get("current_price", {}).get("usd"),
                "market_cap_usd": market_data.get("market_cap", {}).get("usd"),
                "volume_24h_usd": market_data.get("total_volume", {}).get("usd"),
                "price_change_24h_pct": market_data.get("price_change_percentage_24h"),
                "price_change_7d_pct": market_data.get("price_change_percentage_7d"),
                "fully_diluted_valuation": market_data.get("fully_diluted_valuation", {}).get("usd"),
                "circulating_supply": market_data.get("circulating_supply"),
                "total_supply": market_data.get("total_supply"),
                "ath_usd": market_data.get("ath", {}).get("usd"),
                "ath_date": market_data.get("ath_date", {}).get("usd"),
                "source": "coingecko",
            }
    except httpx.TimeoutException:
        logger.debug("coingecko_price_timeout", chain=chain)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.debug("coingecko_price_failed", error=str(e)[:80])

    return {
        "check": "coingecko",
        "result": "unavailable",
        "error": "CoinGecko API unavailable",
        "source": "unavailable",
    }


async def _check_1inch_swap(token_address: str, chain: str, amount_usd: float = 100.0) -> dict[str, Any] | None:
    """Check 1inch swap for token."""
    logger.info("check_1inch", token=token_address, chain=chain, amount_usd=amount_usd)
    return {"check": "1inch", "result": "pending"}
