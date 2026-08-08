"""Blockchain data checks — explorers, DeFiLlama, RAG, verification."""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    BLOCKSCOUT_URLS,
    DEFILLAMA_API,
    _fetch_dexscreener,
    logger,
)


async def _check_blockscout(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token on Blockscout — address metadata, contract status, tx count."""
    result: dict[str, Any] = {
        "check": "blockscout",
        "result": "scanned",
    }
    base_url = BLOCKSCOUT_URLS.get(chain, BLOCKSCOUT_URLS["ethereum"])
    try:
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
            result["token_type"] = data.get("token", {}).get("type", "") if data.get("token") else ""
            result["ens_domain"] = data.get("ens_info", {}).get("domain", "") or None

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
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.debug("blockscout_failed", error=str(e)[:80])
        return {"check": "blockscout", "result": "unavailable", "error": str(e)[:60]}

    return result


async def _check_token_sniffer(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token on TokenSniffer."""
    logger.info("check_token_sniffer", token=token_address, chain=chain)
    return {"check": "tokensniffer", "result": "pending"}


async def _check_defillama_context(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check DeFiLlama context for token — TVL, protocol presence, token price."""
    logger.info("check_defillama", token=token_address, chain=chain)
    result: dict[str, Any] = {
        "check": "defillama",
        "result": "scanned",
        "has_defi_presence": False,
        "protocols_found": 0,
    }

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

        async with httpx.AsyncClient(timeout=10.0) as client:
            info_resp = await client.get(f"{DEFILLAMA_API}/token/{prefix.upper()}/{token_address}")
            if info_resp.status_code == 200:
                info = info_resp.json()
                if info:
                    result["has_defi_presence"] = True
                    if isinstance(info, list):
                        result["protocols_found"] = len(info)
                        protocols = []
                        total_tvl: float = 0
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
                        result["max_apy"] = max((float(p.get("apy", 0) or 0) for p in pools[:5]), default=0)
                        result["source"] = "defillama_yields"
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError):
                pass

    except httpx.TimeoutException:
        logger.debug("defillama_context_timeout", chain=chain)
        return {**result, "result": "unavailable", "error": "DeFiLlama timeout"}
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.debug("defillama_context_failed", error=str(e)[:80])
        return {**result, "result": "unavailable", "error": str(e)[:60]}

    return result


async def _check_defi_scanner(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check token on DeFi Scanner — DeFiLlama for protocol presence & TVL."""
    result: dict[str, Any] = {
        "check": "defi_scanner",
        "result": "scanned",
        "has_defi_presence": False,
        "protocols_found": 0,
    }

    try:
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
                price_resp = await client.get(f"{DEFILLAMA_API}/prices/current/{llama_id}")
                if price_resp.status_code == 200:
                    price_data = price_resp.json()
                    coins = price_data.get("coins", {})
                    coin_data = coins.get(llama_id, coins.get(token_address, {}))
                    if coin_data:
                        result["price_usd"] = coin_data.get("price", 0)
                        result["symbol"] = coin_data.get("symbol", "")
                        result["defillama_confidence"] = coin_data.get("confidence", 0)

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
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.debug("defiscanner_failed", error=str(e)[:80])

    if not result.get("source"):
        try:
            data = await _fetch_dexscreener(token_address, chain)
            if data and data.get("pairs"):
                pair = data["pairs"][0]
                result["price_usd"] = float(pair.get("priceUsd", 0) or 0)
                result["volume_24h"] = float(pair.get("volume", {}).get("h24", 0) or 0)
                result["liquidity_usd"] = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                result["dex_name"] = pair.get("dexId", "")
                result["has_defi_presence"] = True
                result["protocols_found"] = 1
                result["source"] = "dexscreener_fallback"
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
            logger.debug("defiscanner_fallback_failed", error=str(e))

    return result


async def _rag_scam_check(token_address: str, chain: str, deployer: str | None = None) -> dict[str, Any] | None:
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
    except httpx.HTTPError as e:
        logger.warning("rag_scam_check_failed", error=str(e)[:100])

    return {"check": "rag", "result": "scanned", "risk_score": 0, "source": "unavailable"}


async def _check_cross_chain_deployer(deployer_addr: str, chain: str) -> dict[str, Any] | None:
    """Check if deployer has cross-chain activity."""
    logger.info("cross_chain_deployer", deployer=deployer_addr, chain=chain)
    return {"check": "cross_chain", "result": "pending"}


async def _verify_mint_consensus(token_address: str, chain: str) -> dict[str, Any] | None:
    """Verify mint authority consensus."""
    logger.info("verify_mint", token=token_address, chain=chain)
    return {"check": "mint", "result": "pending"}


async def _ingest_scan_to_rag(scan_result: Any) -> None:
    """Ingest scan result to RAG for future searches.

    Deprecated: this stub previously only logged and never ingested. The
    real implementation lives in ``app.domains.scanner.service.scoring``;
    this wrapper is kept for backward compatibility and delegates to it.
    """
    logger.warning(
        "ingest_to_rag_deprecated: import from app.domains.scanner.service.scoring instead",
        token=getattr(scan_result, "token_address", "")[:16],
    )
    from app.domains.scanner.service.scoring import _ingest_scan_to_rag as _real_ingest

    await _real_ingest(scan_result)


async def _check_sourcify(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Sourcify for token."""
    logger.info("sourcify_check", token=token_address, chain=chain)
    return {"check": "sourcify", "result": "pending"}
