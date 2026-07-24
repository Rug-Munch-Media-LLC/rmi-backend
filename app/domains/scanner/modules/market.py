"""Scanner module — split from modules.py for maintainability."""

from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    BLOCKSCOUT_URLS,
    DEFILLAMA_API,
    GOPLUS_CHAIN_IDS,
    _fetch_json,
    _fetch_with_fallback,
    logger,
)


async def _get_price_consensus(
    token_address: str, chain: str, market_price: float = 0
) -> dict[str, Any] | None:
    """Get price consensus across multiple sources."""
    result: dict[str, Any] = {
        "check": "price",
        "result": "scanned",
        "price_usd": 0,
        "sources": [],
        "price_consensus": False,
    }

    prices: dict[str, float] = {}

    # Source 1: DexScreener
    try:
        data, _ = await _fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            price = float(pair.get("priceUsd", 0) or 0)
            if price > 0:
                prices["dexscreener"] = price
                result["price_usd"] = price
                result["volume_24h"] = float(pair.get("volume", {}).get("h24", 0) or 0)
                result["liquidity_usd"] = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                result["fdv"] = float(pair.get("fdv", 0) or 0)
                result["price_change_24h"] = pair.get("priceChange", {}).get("h24", 0)
                result["dex"] = pair.get("dexId", "")
                result["pair_address"] = pair.get("pairAddress", "")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("dexscreener_price_failed", error=str(e))

    # Source 2: GoPlus (has price info for some tokens)
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
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("goplus_price_failed", error=str(e))

    # Determine consensus
    result["sources"] = list(prices.keys())
    if len(prices) >= 1:
        result["price_usd"] = max(prices.values())  # Use highest confidence
        result["price_consensus"] = True

    return result


# ── 10. Spam/Malicious Token Check ────────────────────────────────────


async def _check_volume_liquidity_ratio(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check volume-to-liquidity ratio.

    volume_24h / liquidity_usd = turnover ratio.
    High ratio (> 5) = potential wash trading (suspicious).
    Very high ratio (> 20) = almost certainly wash trading.
    Low ratio (< 0.5) + low absolute values = dead token.
    """
    result: dict[str, Any] = {
        "check": "volume_liquidity_ratio",
        "result": "scanned",
        "volume_24h": 0.0,
        "liquidity_usd": 0.0,
        "ratio": 0.0,
        "risk_level": "unknown",
        "is_wash_trading_suspicious": False,
        "risk_score": 0,
    }

    # Fetch DexScreener data for volume + liquidity
    try:
        data, _ = await _fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            volume = float(pair.get("volume", {}).get("h24", 0) or 0)
            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            price_usd = float(pair.get("priceUsd", 0) or 0)

            result["volume_24h"] = volume
            result["liquidity_usd"] = liquidity
            result["price_usd"] = price_usd
            result["source"] = "dexscreener"

            # Calculate ratio (avoid division by zero)
            if liquidity > 0 and volume > 0:
                ratio = volume / liquidity
                result["ratio"] = round(ratio, 4)
            elif volume > 0 and liquidity == 0:
                result["ratio"] = float("inf")
            else:
                result["ratio"] = 0.0

            # Risk classification
            ratio = result["ratio"]
            if ratio == float("inf") or ratio > 50:
                result["risk_level"] = "critical"
                result["is_wash_trading_suspicious"] = True
                result["risk_score"] = 45
            elif ratio > 20:
                result["risk_level"] = "high"
                result["is_wash_trading_suspicious"] = True
                result["risk_score"] = 35
            elif ratio > 5:
                result["risk_level"] = "medium"
                result["risk_score"] = 20
                # Check if it's suspicious based on context
                if volume > 10000 and liquidity < 1000:
                    result["is_wash_trading_suspicious"] = True
                    result["risk_score"] = 35
                    result["risk_level"] = "high"
            elif ratio < 0.5 and volume < 100:
                result["risk_level"] = "low_activity"
                result["risk_score"] = 5
            else:
                result["risk_level"] = "healthy"
                result["risk_score"] = 0

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("volume_liquidity_failed", error=str(e)[:60])
        result["result"] = "error"
        result["error"] = str(e)[:60]

    return result


# ── Legacy stub modules (unchanged - return pending but are non-critical) ──


async def _check_blockscout(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token on Blockscout — address metadata, contract status, tx count."""
    result: dict[str, Any] = {
        "check": "blockscout",
        "result": "scanned",
    }
    base_url = BLOCKSCOUT_URLS.get(chain, BLOCKSCOUT_URLS["ethereum"])
    try:
        # Get address info
        url = f"{base_url}/api/v2/addresses/{token_address}"
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                result["result"] = "unavailable"
                result["error"] = f"Blockscout HTTP {resp.status_code}"
                return result

            data = resp.json()
            result["is_contract"] = data.get("is_contract", False)
            result["has_transactions"] = (data.get("tx_count", 0) or 0) > 0
            result["tx_count"] = data.get("tx_count", 0)
            result["token_type"] = (
                data.get("token", {}).get("type", "") if data.get("token") else ""
            )
            result["ens_domain"] = data.get("ens_info", {}).get("domain", "") or None

            # If it's a token, get market data
            if data.get("token"):
                token_data = data["token"]
                result["symbol"] = token_data.get("symbol", "")
                result["name"] = token_data.get("name", "")
                result["decimals"] = token_data.get("decimals")
                result["total_supply"] = token_data.get("total_supply", "")
                result["holders"] = token_data.get("holders", "")
                result["exchange_rate"] = token_data.get("exchange_rate")

            result["source"] = "blockscout"
            result["chain_url"] = base_url
    except httpx.TimeoutException:
        logger.debug("blockscout_timeout", chain=chain)
        return {"check": "blockscout", "result": "unavailable", "error": "timeout"}
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("blockscout_failed", error=str(e)[:80])
        return {"check": "blockscout", "result": "unavailable", "error": str(e)[:60]}

    return result


async def _check_token_sniffer(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token on TokenSniffer."""
    logger.info("check_token_sniffer", token=token_address, chain=chain)
    return {"check": "tokensniffer", "result": "pending"}


async def _check_trm_sanctions(address: str) -> dict[str, Any] | None:
    """Check address on TRM sanctions list."""
    logger.info("check_trm_sanctions", address=address[:10])
    return {"check": "trm", "result": "pending"}


async def _check_scorechain_sanctions(
    address: str, chain: str = "ethereum"
) -> dict[str, Any] | None:
    """Check address on Scorechain sanctions list."""
    logger.info("check_scorechain", address=address[:10], chain=chain)
    return {"check": "scorechain", "result": "pending"}


async def _check_chainabuse(address: str) -> dict[str, Any] | None:
    """Check address on Chainabuse."""
    logger.info("check_chainabuse", address=address[:10])
    return {"check": "chainabuse", "result": "pending"}


async def _check_forta_alerts(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Forta alerts for token."""
    logger.info("check_forta", token=token_address, chain=chain)
    return {"check": "forta", "result": "pending"}


async def _check_defillama_context(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check DeFiLlama context for token — TVL, protocol presence, token price.

    Uses the free DeFiLlama API to check:
    - Current token price (via /prices/current/)
    - Protocol/TVL data (via /token/ endpoint)
    - Yield/farming pools for the token (via /yields/pools)

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with price, defi_presence, protocols, tvl data, or unavailable status
    """
    logger.info("check_defillama", token=token_address, chain=chain)
    result: dict[str, Any] = {
        "check": "defillama",
        "result": "scanned",
        "has_defi_presence": False,
        "protocols_found": 0,
    }

    # Build DeFiLlama-style chain prefix
    chain_prefix_map: dict[str, str] = {
        "ethereum": "ethereum",
        "eth": "ethereum",
        "solana": "solana",
        "bsc": "bsc",
        "binance": "bsc",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "optimism": "optimism",
        "base": "base",
        "avalanche": "avalanche",
        "fantom": "fantom",
    }
    prefix = chain_prefix_map.get(chain)
    if not prefix:
        return {**result, "result": "unavailable", "reason": f"Chain {chain} not supported"}

    llama_id = f"{prefix}:{token_address}"

    try:
        # Source 1: Current price from DeFiLlama
        async with httpx.AsyncClient(timeout=10.0) as client:
            price_resp = await client.get(f"{DEFILLAMA_API}/prices/current/{llama_id}")
            if price_resp.status_code == 200:
                price_data = price_resp.json()
                coins = price_data.get("coins", {})
                coin_data = coins.get(llama_id, coins.get(token_address, {}))
                if coin_data:
                    result["price_usd"] = coin_data.get("price", 0)
                    result["symbol"] = coin_data.get("symbol", "")
                    result["confidence"] = coin_data.get("confidence", 0)
                    result["source"] = "defillama_price"

        # Source 2: Token info / protocol presence
        async with httpx.AsyncClient(timeout=10.0) as client:
            info_resp = await client.get(f"{DEFILLAMA_API}/token/{prefix.upper()}/{token_address}")
            if info_resp.status_code == 200:
                info = info_resp.json()
                if info:
                    result["has_defi_presence"] = True
                    if isinstance(info, list):
                        result["protocols_found"] = len(info)
                        # Extract protocol names
                        protocols = []
                        total_tvl = 0
                        for p in info[:10]:
                            protocols.append(
                                {
                                    "name": p.get("name", ""),
                                    "tvl": p.get("tvl", 0) or p.get("totalLiquidity", 0),
                                }
                            )
                            tvl_val = p.get("tvl", 0) or p.get("totalLiquidity", 0) or 0
                            if isinstance(tvl_val, (int, float)):
                                total_tvl += tvl_val
                        result["protocols"] = protocols
                        result["total_tvl_usd"] = total_tvl
                    else:
                        result["protocols_found"] = 1
                        result["protocol_name"] = info.get("name", "")
                        result["protocol_tvl"] = info.get("tvl", 0)
                    if not result.get("source"):
                        result["source"] = "defillama_token"

        # Source 3: Yield pools (if token is in yield/farming)
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                yield_resp = await client.get(
                    f"{DEFILLAMA_API}/yields/pools",
                    params={"token": token_address},
                )
                if yield_resp.status_code == 200:
                    yield_data = yield_resp.json()
                    pools = yield_data.get("data", [])
                    if pools:
                        result["yield_pools_found"] = len(pools)
                        result["max_apy"] = max(
                            (float(p.get("apy", 0) or 0) for p in pools[:5]), default=0
                        )
                        result["source"] = "defillama_yields"
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                pass

    except httpx.TimeoutException:
        logger.debug("defillama_context_timeout", chain=chain)
        return {**result, "result": "unavailable", "error": "DeFiLlama timeout"}
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("defillama_context_failed", error=str(e)[:80])
        return {**result, "result": "unavailable", "error": str(e)[:60]}

    return result


async def _check_coingecko_price(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check CoinGecko price for token.

    Uses the free CoinGecko API to get current price, market cap,
    24h volume, and price change data.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with price, market_cap, volume_24h, price_change_24h,
        or unavailable if token not indexed by CoinGecko.
    """
    logger.info("check_coingecko", token=token_address, chain=chain)

    # Map chain to CoinGecko platform ID
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
            "result": "unavailable",
            "reason": f"Chain {chain} not on CoinGecko",
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
                }
            if resp.status_code != 200:
                return {
                    "check": "coingecko",
                    "result": "unavailable",
                    "error": f"HTTP {resp.status_code}",
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
                "fully_diluted_valuation": market_data.get("fully_diluted_valuation", {}).get(
                    "usd"
                ),
                "circulating_supply": market_data.get("circulating_supply"),
                "total_supply": market_data.get("total_supply"),
                "ath_usd": market_data.get("ath", {}).get("usd"),
                "ath_date": market_data.get("ath_date", {}).get("usd"),
                "source": "coingecko",
            }
    except httpx.TimeoutException:
        logger.debug("coingecko_price_timeout", chain=chain)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("coingecko_price_failed", error=str(e)[:80])

    return {"check": "coingecko", "result": "unavailable", "error": "CoinGecko API unavailable"}


async def _check_1inch_swap(
    token_address: str, chain: str, amount_usd: float = 100.0
) -> dict[str, Any] | None:
    """Check 1inch swap for token."""
    logger.info("check_1inch", token=token_address, chain=chain, amount_usd=amount_usd)
    return {"check": "1inch", "result": "pending"}


async def _rag_scam_check(
    token_address: str, chain: str, deployer: str | None = None
) -> dict[str, Any] | None:
    """Check RAG for scam indicators via vector search + pattern matching."""
    logger.info("rag_scam_check", token=token_address, chain=chain, deployer=deployer)
    try:
        from app.domains.scanner.rag_scam_check import check_scam

        result = await check_scam(
            token_address=token_address,
            chain=chain,
            symbol="",
            name="",
            deployer=deployer,
        )
        if result:
            return result
    except ImportError:
        logger.debug("rag_scam_check_module_not_available")
    except (httpx.HTTPError) as e :
        logger.warning("rag_scam_check_failed", error=str(e)[:100])

    return {"check": "rag", "result": "scanned", "risk_score": 0, "source": "unavailable"}


async def _check_cross_chain_deployer(deployer_addr: str, chain: str) -> dict[str, Any] | None:
    """Check if deployer has cross-chain activity."""
    logger.info("cross_chain_deployer", deployer=deployer_addr, chain=chain)
    return {"check": "cross_chain", "result": "pending"}


async def _get_birdeye_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Birdeye data for token."""
    logger.info("birdeye_data", token=token_address, chain=chain)
    return {"check": "birdeye", "result": "pending"}


async def _verify_mint_consensus(token_address: str, chain: str) -> dict[str, Any] | None:
    """Verify mint authority consensus."""
    logger.info("verify_mint", token=token_address, chain=chain)
    return {"check": "mint", "result": "pending"}


async def _ingest_scan_to_rag(scan_result: Any) -> None:
    """Ingest scan result to RAG for future searches."""
    logger.info("ingest_to_rag", token=scan_result.token_address)


async def _get_solscan_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Solscan data for token."""
    logger.info("solscan_data", token=token_address, chain=chain)
    return {"check": "solscan", "result": "pending"}


async def _get_moralis_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Moralis data for token."""
    logger.info("moralis_data", token=token_address, chain=chain)
    return {"check": "moralis", "result": "pending"}


async def _get_etherscan_enhanced(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get enhanced Etherscan data for token."""
    logger.info("etherscan_enhanced", token=token_address, chain=chain)
    return {"check": "etherscan", "result": "pending"}


async def _get_quicknode_pumpfun(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Quicknode Pump.fun data for token."""
    logger.info("quicknode_pumpfun", token=token_address, chain=chain)
    return {"check": "quicknode", "result": "pending"}


async def _check_nansen(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Nansen data for token."""
    logger.info("nansen_check", token=token_address, chain=chain)
    return {"check": "nansen", "result": "pending"}


async def _check_chainaware(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check ChainAware data for token."""
    logger.info("chainaware_check", token=token_address, chain=chain)
    return {"check": "chainaware", "result": "pending"}


async def _check_blowfish(
    token_address: str, chain: str, user_address: str = ""
) -> dict[str, Any] | None:
    """Check Blowfish AI for token."""
    logger.info("blowfish_check", token=token_address, chain=chain, user=user_address)
    return {"check": "blowfish", "result": "pending"}


async def _check_santiment(symbol: str) -> dict[str, Any] | None:
    """Check Santiment for token."""
    logger.info("santiment_check", symbol=symbol)
    return {"check": "santiment", "result": "pending"}


async def _check_fear_greed() -> dict[str, Any] | None:
    """Check Crypto Fear & Greed index."""
    logger.info("fear_greed_check")
    return {"check": "fear_greed", "result": "pending"}


async def _check_news_sentiment(symbol: str) -> dict[str, Any] | None:
    """Check news sentiment for token."""
    logger.info("news_sentiment_check", symbol=symbol)
    return {"check": "news", "result": "pending"}


async def _check_augmento(symbol: str) -> dict[str, Any] | None:
    """Check Augmento for token."""
    logger.info("augmento_check", symbol=symbol)
    return {"check": "augmento", "result": "pending"}


async def _check_coingecko_trending() -> dict[str, Any] | None:
    """Check CoinGecko trending tokens."""
    logger.info("coingecko_trending")
    return {"check": "trending", "result": "pending"}


async def _check_webacy(address: str, chain: str) -> dict[str, Any] | None:
    """Check Webacy for address."""
    logger.info("webacy_check", address=address, chain=chain)
    return {"check": "webacy", "result": "pending"}


async def _check_sourcify(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Sourcify for token."""
    logger.info("sourcify_check", token=token_address, chain=chain)
    return {"check": "sourcify", "result": "pending"}


async def _check_scamsniffer_live(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check ScamSniffer Live for token."""
    logger.info("scamsniffer_live", token=token_address, chain=chain)
    return {"check": "scamsniffer", "result": "pending"}


async def _check_dune(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Dune analytics for token."""
    logger.info("dune_check", token=token_address, chain=chain)
    return {"check": "dune", "result": "pending"}


async def _check_arkham(address: str, chain: str) -> dict[str, Any] | None:
    """Check Arkham for address."""
    logger.info("arkham_check", address=address, chain=chain)
    return {"check": "arkham", "result": "pending"}


async def _check_thegraph(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check The Graph for token."""
    logger.info("thegraph_check", token=token_address, chain=chain)
    return {"check": "thegraph", "result": "pending"}


async def _check_chainpatrol_asset(asset_type: str, asset_id: str) -> dict[str, Any] | None:
    """Check asset on ChainPatrol."""
    logger.info("check_chainpatrol", type=asset_type, id=asset_id)
    return {"check": "chainpatrol", "result": "pending"}


async def _check_defi_scanner(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token on DeFi Scanner — DeFiLlama for protocol presence & TVL."""
    result: dict[str, Any] = {
        "check": "defi_scanner",
        "result": "scanned",
        "has_defi_presence": False,
        "protocols_found": 0,
    }

    try:
        # Try DeFiLlama current price endpoint (works for any token with a known price)
        if chain == "ethereum" and token_address.startswith("0x"):
            llama_id = f"ethereum:{token_address}"
        elif chain == "solana":
            llama_id = f"solana:{token_address}"
        elif chain == "bsc":
            llama_id = f"bsc:{token_address}"
        else:
            llama_id = None

        if llama_id:
            async with httpx.AsyncClient(timeout=10.0) as client:
                price_resp = await client.get(
                    f"{DEFILLAMA_API}/prices/current/{llama_id}",
                )
                if price_resp.status_code == 200:
                    price_data = price_resp.json()
                    coins = price_data.get("coins", {})
                    coin_data = coins.get(llama_id, coins.get(token_address, {}))
                    if coin_data:
                        result["price_usd"] = coin_data.get("price", 0)
                        result["symbol"] = coin_data.get("symbol", "")
                        result["defillama_confidence"] = coin_data.get("confidence", 0)

        # Check simple token info endpoint
        async with httpx.AsyncClient(timeout=10.0) as client:
            info_resp = await client.get(f"{DEFILLAMA_API}/token/{chain.upper()}/{token_address}")
            if info_resp.status_code == 200:
                info = info_resp.json()
                if info:
                    result["has_defi_presence"] = True
                    result["protocols_found"] = len(info) if isinstance(info, list) else 1
                    result["protocols"] = info[:5] if isinstance(info, list) else [info]
                    result["source"] = "defillama"

    except httpx.TimeoutException:
        logger.debug("defiscanner_timeout", chain=chain)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("defiscanner_failed", error=str(e)[:80])

    # If DeFiLlama had no data, try DexScreener as fallback
    if not result.get("source"):
        try:
            data, _ = await _fetch_with_fallback(
                [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"]
            )
            if data and data.get("pairs"):
                pair = data["pairs"][0]
                result["price_usd"] = float(pair.get("priceUsd", 0) or 0)
                result["volume_24h"] = float(pair.get("volume", {}).get("h24", 0) or 0)
                result["liquidity_usd"] = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                result["dex_name"] = pair.get("dexId", "")
                result["has_defi_presence"] = True
                result["protocols_found"] = 1
                result["source"] = "dexscreener_fallback"
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("defiscanner_fallback_failed", error=str(e))

    return result


async def _check_volume_anomaly(
    token_address: str,
    chain: str,
    market_price: float = 0,
    volume_24h: float = 0,
    circulating_supply: float = 0,
) -> dict[str, Any] | None:
    """Detect wash-trading and volume anomalies using DexScreener data.

    Analyzes buy/sell asymmetry, volume/liquidity ratios, and price-volume
    divergence patterns to flag suspicious trading activity.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier
        market_price: Pre-fetched market price (optional — fetched from DexScreener if 0)
        volume_24h: Pre-fetched 24h volume (optional)
        circulating_supply: Pre-fetched circulating supply (optional)

    Returns:
        Dict with anomaly_score, anomalies list, is_suspicious flag, and signals
    """
    logger.info("check_volume_anomaly", token=token_address, chain=chain)
    result: dict[str, Any] = {
        "check": "volume_anomaly",
        "result": "scanned",
        "anomaly_score": 0,
        "anomalies": [],
        "is_suspicious": False,
        "signals": {},
    }

    try:
        data, _ = await _fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"],
            json_mode=True,
        )
        if not data or not data.get("pairs"):
            # Use provided params as fallback
            if volume_24h > 0 and market_price > 0:
                result["total_volume_24h"] = volume_24h
                result["market_price"] = market_price
                result["source"] = "params"
                return result
            result["result"] = "unavailable"
            result["error"] = "No DexScreener data available"
            return result

        pairs = data["pairs"]
        total_volume = 0.0
        total_liquidity = 0.0
        buy_count = 0
        sell_count = 0
        price_changes: list[float] = []
        prices: list[float] = []

        for pair in pairs:
            try:
                vol = float(pair.get("volume", {}).get("h24", 0) or 0)
                liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                price = float(pair.get("priceUsd", 0) or 0)
                txns = pair.get("txns", {}).get("h24", {})
                buys = int(txns.get("buys", 0) or 0)
                sells = int(txns.get("sells", 0) or 0)
                price_change = pair.get("priceChange", {}).get("h24", 0) or 0

                total_volume += vol
                total_liquidity += liq
                buy_count += buys
                sell_count += sells
                if price > 0:
                    prices.append(price)
                if price_change != 0:
                    price_changes.append(price_change)
            except (ValueError, TypeError):
                continue

        signals: dict[str, Any] = {}
        anomaly_score = 0
        anomalies: list[str] = []

        # Signal 1: Volume/Liquidity Ratio
        if total_liquidity > 0 and total_volume > 0:
            vol_liq_ratio = total_volume / total_liquidity
            signals["volume_liquidity_ratio"] = round(vol_liq_ratio, 2)
            if vol_liq_ratio > 50:
                anomalies.append("extreme_volume_liquidity_ratio")
                anomaly_score += 40
            elif vol_liq_ratio > 20:
                anomalies.append("high_volume_liquidity_ratio")
                anomaly_score += 25
            elif vol_liq_ratio > 5:
                anomalies.append("elevated_volume_liquidity_ratio")
                anomaly_score += 10

        # Signal 2: Buy/Sell Asymmetry
        total_txns = buy_count + sell_count
        if total_txns > 0:
            buy_ratio = buy_count / total_txns
            signals["buy_ratio"] = round(buy_ratio, 3)
            if buy_ratio > 0.95:
                anomalies.append("extreme_buy_dominance")
                anomaly_score += 35
            elif buy_ratio > 0.85:
                anomalies.append("high_buy_dominance")
                anomaly_score += 20
            elif buy_ratio < 0.05:
                anomalies.append("extreme_sell_dominance")
                anomaly_score += 35
            elif buy_ratio < 0.15:
                anomalies.append("high_sell_dominance")
                anomaly_score += 20

        # Signal 3: Negative price change + high volume = distribution
        if price_changes and total_volume > 0:
            avg_price_change = sum(price_changes) / len(price_changes)
            signals["avg_price_change_24h"] = round(avg_price_change, 2)
            if avg_price_change < -20 and total_volume > 10000:
                anomalies.append("price_volume_divergence")
                anomaly_score += 30

        result["anomaly_score"] = min(100, anomaly_score)
        result["is_suspicious"] = anomaly_score >= 30
        result["anomalies"] = anomalies
        result["signals"] = signals
        result["risk_level"] = (
            "critical"
            if anomaly_score >= 70
            else "high"
            if anomaly_score >= 40
            else "medium"
            if anomaly_score >= 20
            else "low"
        )
        result["total_volume_24h"] = total_volume
        result["total_liquidity_usd"] = total_liquidity
        result["buy_count_24h"] = buy_count
        result["sell_count_24h"] = sell_count
        result["source"] = "dexscreener"

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("volume_anomaly_failed", error=str(e)[:80])
        result["result"] = "error"
        result["error"] = str(e)[:60]

    return result
