"""Scanner service — main scan_token function and entry points."""

import asyncio
from typing import Any

from app.core.data_fusion import fusion
from app.core.logging import get_logger
from app.domains.scanner.modules import (
    _check_bundle,
    _check_contract_verification,
    _check_copycat,
    _check_goplus_security,
    _check_honeypot_is,
    _check_liquidity_lock_multi,
    _check_lp_concentration,
    _check_ownership_renounced,
    _check_social_sentiment,
    _check_spam,
    _check_token_age,
    _check_volume_liquidity_ratio,
    _get_deployer_info,
    _get_holder_data,
    _get_price_consensus,
)
from app.domains.scanner.rag_scam_check import check_scam as _rag_check_scam

logger = get_logger(__name__)

# Re-export ScanResult for backward compatibility
from app.domains.scanner.market_data import fetch_market_data  # noqa: E402
from app.domains.scanner.models import ScanResult  # noqa: E402
from app.domains.scanner.modules import (  # noqa: E402
    _check_blockscout,
    _check_defi_scanner,
)


async def _rag_scam_check(
    token_address: str, chain: str, deployer: str | None = None
) -> dict[str, Any] | None:
    """Check RAG for scam indicators via vector search + pattern matching."""
    logger.info("rag_scam_check", token=token_address, chain=chain, deployer=deployer)
    try:
        result = await _rag_check_scam(
            token_address=token_address,
            chain=chain,
            deployer=deployer,
        )
        if result:
            return result
    except Exception as e:
        logger.warning("rag_scam_check_failed", error=str(e)[:100])

    return {"check": "rag", "result": "scanned", "risk_score": 0, "source": "unavailable"}


async def _check_cross_chain_deployer(deployer_addr: str, chain: str) -> dict[str, Any] | None:
    """Check if deployer has cross-chain activity.

    Args:
        deployer_addr: Deployer wallet address
        chain: Blockchain network identifier

    Returns:
        Dict with check result or None if not applicable

    Note:
        Stub — requires cross-chain indexer integration. Returns "unavailable"
        until cross-chain entity resolution is deployed.
    """
    logger.info("cross_chain_deployer", deployer=deployer_addr, chain=chain)
    return {
        "check": "cross_chain",
        "result": "unavailable",
        "reason": "Cross-chain indexer not yet deployed",
    }


async def _check_volume_anomaly(
    token_address: str,
    chain: str,
    market_price: float = 0,
    volume_24h: float = 0,
    circulating_supply: float = 0,
) -> dict[str, Any] | None:
    """Detect wash-trading volume anomalies using DexScreener multi-source analysis.

    Uses DexScreener data to detect:
    - Volume/liquidity ratio anomalies (washing through same pools)
    - Price-volume divergence (price drops on high volume = distribution)
    - Unusual buy/sell ratio asymmetry (bots front-running)
    - Stake/transfer volume disparity (on-chain vs DEX volume mismatch)

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier
        market_price: Current market price in USD (optional)
        volume_24h: 24-hour trading volume in USD (optional)
        circulating_supply: Circulating token supply (optional)

    Returns:
        Dict with anomaly detection results, risk_score, and flagged patterns
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
        from app.domains.scanner.modules._core import _fetch_with_fallback

        data, _ = await _fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"],
            json_mode=True,
        )
        if not data or not data.get("pairs"):
            result["result"] = "unavailable"
            result["error"] = "No DexScreener data available"
            return result

        pairs = data["pairs"]
        total_volume = 0.0
        total_liquidity = 0.0
        buy_volume = 0.0
        sell_volume = 0.0
        buy_count = 0
        sell_count = 0
        price_changes = []
        prices = []

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
                buy_volume += buys * price if price > 0 else 0
                sell_volume += sells * price if price > 0 else 0
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

        # ── Signal 1: Volume/Liquidity Ratio ──
        if total_liquidity > 0 and total_volume > 0:
            vol_liq_ratio = total_volume / total_liquidity
            signals["volume_liquidity_ratio"] = round(vol_liq_ratio, 2)
            if vol_liq_ratio > 50:
                anomalies.append("extreme_volume_liquidity_ratio")
                anomaly_score += 40
                signals["vol_liq_risk"] = "critical"
            elif vol_liq_ratio > 20:
                anomalies.append("high_volume_liquidity_ratio")
                anomaly_score += 25
                signals["vol_liq_risk"] = "high"
            elif vol_liq_ratio > 5:
                anomalies.append("elevated_volume_liquidity_ratio")
                anomaly_score += 10
                signals["vol_liq_risk"] = "medium"
            else:
                signals["vol_liq_risk"] = "normal"

        # ── Signal 2: Buy/Sell Asymmetry ──
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

        # ── Signal 3: Price-Volume Divergence ──
        if price_changes and total_volume > 0:
            avg_price_change = sum(price_changes) / len(price_changes)
            signals["avg_price_change_24h"] = round(avg_price_change, 2)
            # Negative price change with high volume = distribution
            if avg_price_change < -20 and total_volume > 10000:
                anomalies.append("price_volume_divergence_distribution")
                anomaly_score += 30
                signals["divergence_type"] = "distribution"
            # Extreme price movement
            if abs(avg_price_change) > 90:
                anomalies.append("extreme_price_movement")
                anomaly_score += 20
                signals["divergence_type"] = "extreme"

        # ── Signal 4: Volume Concentration Across Pools ──
        if len(pairs) > 1:
            top_pool_vol = max(
                (float(p.get("volume", {}).get("h24", 0) or 0) for p in pairs), default=0
            )
            if total_volume > 0:
                top_pool_share = top_pool_vol / total_volume
                signals["top_pool_volume_share"] = round(top_pool_share, 3)
                if top_pool_share > 0.95:
                    anomalies.append("volume_concentrated_single_pool")
                    anomaly_score += 15
                    signals["pool_concentration_risk"] = "critical"
                elif top_pool_share > 0.80:
                    anomalies.append("high_volume_concentration")
                    anomaly_score += 8
                    signals["pool_concentration_risk"] = "high"

        # ── Signal 5: Price Consistency Across Pools ──
        if len(prices) > 1:
            price_range = max(prices) - min(prices)
            avg_price = sum(prices) / len(prices)
            if avg_price > 0:
                price_spread_pct = (price_range / avg_price) * 100
                signals["price_spread_pct"] = round(price_spread_pct, 2)
                if price_spread_pct > 20:
                    anomalies.append("significant_price_spread_across_pools")
                    anomaly_score += 10
                elif price_spread_pct > 50:
                    anomalies.append("extreme_price_spread_across_pools")
                    anomaly_score += 20

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

    except Exception as e:
        logger.debug("volume_anomaly_failed", error=str(e)[:80])
        result["result"] = "error"
        result["error"] = str(e)[:60]

    return result


async def _get_birdeye_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Birdeye market data for token (price, volume, liquidity).

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Birdeye data or unavailable status

    Note:
        Stub — requires Birdeye API key. Returns "unavailable" until configured.
    """
    logger.info("birdeye_data", token=token_address, chain=chain)
    return {"check": "birdeye", "result": "unavailable", "reason": "Birdeye API not configured"}


async def _verify_mint_consensus(token_address: str, chain: str) -> dict[str, Any] | None:
    """Verify mint authority across multiple RPC sources.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with consensus verification result

    Note:
        Stub — requires multi-RPC mint authority verification.
    """
    logger.info("verify_mint", token=token_address, chain=chain)
    return {
        "check": "mint",
        "result": "unavailable",
        "reason": "Multi-RPC mint verification not deployed",
    }


async def _ingest_scan_to_rag(scan_result: Any) -> None:
    """Ingest scan result to RAG for future similarity searches.

    Args:
        scan_result: The completed ScanResult to persist
    """
    logger.info("ingest_to_rag", token=scan_result.token_address)


async def _get_solscan_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Solscan enhanced data for Solana tokens.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Solscan data or unavailable status
    """
    logger.info("solscan_data", token=token_address, chain=chain)
    return {"check": "solscan", "result": "unavailable", "reason": "Solscan API not configured"}


async def _get_moralis_data(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Moralis enriched token data.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Moralis data or unavailable status
    """
    logger.info("moralis_data", token=token_address, chain=chain)
    return {"check": "moralis", "result": "unavailable", "reason": "Moralis API not configured"}


async def _get_etherscan_enhanced(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get enhanced Etherscan data (source code, ABI, events).

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Etherscan data or unavailable status
    """
    logger.info("etherscan_enhanced", token=token_address, chain=chain)
    return {
        "check": "etherscan",
        "result": "unavailable",
        "reason": "Etherscan API key not configured",
    }


async def _get_quicknode_pumpfun(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get Quicknode Pump.fun data for Solana meme tokens.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Quicknode data or unavailable status
    """
    logger.info("quicknode_pumpfun", token=token_address, chain=chain)
    return {
        "check": "quicknode",
        "result": "unavailable",
        "reason": "Quicknode Pump.fun API not configured",
    }


async def _check_nansen(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Nansen label data for token/address.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Nansen labels or unavailable status
    """
    logger.info("nansen_check", token=token_address, chain=chain)
    return {"check": "nansen", "result": "unavailable", "reason": "Nansen API not configured"}


async def _check_chainaware(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check ChainAware risk data for token.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with ChainAware data or unavailable status
    """
    logger.info("chainaware_check", token=token_address, chain=chain)
    return {"check": "chainaware", "result": "unavailable", "reason": "ChainAware not integrated"}


async def _check_blowfish(
    token_address: str, chain: str, user_address: str = ""
) -> dict[str, Any] | None:
    """Check Blowfish AI security scan for token.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier
        user_address: Optional user wallet for context

    Returns:
        Dict with Blowfish result or unavailable status
    """
    logger.info("blowfish_check", token=token_address, chain=chain, user=user_address)
    return {"check": "blowfish", "result": "unavailable", "reason": "Blowfish API not configured"}


async def _check_santiment(symbol: str) -> dict[str, Any] | None:
    """Check Santiment social metrics for token symbol.

    Args:
        symbol: Token ticker symbol

    Returns:
        Dict with Santiment data or unavailable status
    """
    logger.info("santiment_check", symbol=symbol)
    return {"check": "santiment", "result": "unavailable", "reason": "Santiment API not configured"}


async def _check_fear_greed() -> dict[str, Any] | None:
    """Check Crypto Fear & Greed market sentiment index.

    Uses the free alternative.me Fear & Greed API (no auth required).
    Returns current index value, classification, and 30-day trend.

    Returns:
        Dict with fear/greed value, classification, timestamp, and trend data
    """
    logger.info("fear_greed_check")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.alternative.me/fng/?limit=30&format=json")
            if resp.status_code != 200:
                logger.debug("fng_http_error", status=resp.status_code)
                return {
                    "check": "fear_greed",
                    "result": "unavailable",
                    "error": f"HTTP {resp.status_code}",
                }

            data = resp.json()
            fng_data = data.get("data", [])
            if not fng_data:
                return {"check": "fear_greed", "result": "unavailable", "error": "No data returned"}

            # Current value
            current = fng_data[0]
            value = int(current.get("value", 50))
            classification = current.get("value_classification", "Neutral")

            # Calculate 30-day trend
            values_30d = []
            classifications_30d = []
            for entry in fng_data:
                try:
                    values_30d.append(int(entry.get("value", 50)))
                    classifications_30d.append(entry.get("value_classification", ""))
                except (ValueError, TypeError):
                    continue

            avg_30d = sum(values_30d) / len(values_30d) if values_30d else 50
            trend = (
                "improving" if value > avg_30d else ("declining" if value < avg_30d else "stable")
            )

            # Min/max range
            min_val = min(values_30d) if values_30d else value
            max_val = max(values_30d) if values_30d else value

            return {
                "check": "fear_greed",
                "result": "scanned",
                "value": value,
                "classification": classification,
                "timestamp": current.get("timestamp", ""),
                "trend_30d": trend,
                "avg_30d": round(avg_30d, 1),
                "min_30d": min_val,
                "max_30d": max_val,
                "source": "alternative.me",
            }
    except httpx.TimeoutException:
        logger.debug("fng_timeout")
    except Exception as e:
        logger.warning("fng_failed", error=str(e)[:100])

    return {"check": "fear_greed", "result": "unavailable", "error": "Fear & Greed API unavailable"}


async def _check_news_sentiment(symbol: str) -> dict[str, Any] | None:
    """Check recent news sentiment for token symbol.

    Args:
        symbol: Token ticker symbol

    Returns:
        Dict with news sentiment or unavailable status
    """
    logger.info("news_sentiment_check", symbol=symbol)
    return {
        "check": "news",
        "result": "unavailable",
        "reason": "News sentiment pipeline not configured",
    }


async def _check_augmento(symbol: str) -> dict[str, Any] | None:
    """Check Augmento AI analysis for token.

    Args:
        symbol: Token ticker symbol

    Returns:
        Dict with Augmento result or unavailable status
    """
    logger.info("augmento_check", symbol=symbol)
    return {"check": "augmento", "result": "unavailable", "reason": "Augmento API not configured"}


async def _check_coingecko_trending() -> dict[str, Any] | None:
    """Check CoinGecko trending tokens list.

    Uses the free CoinGecko API (/search/trending) to get the top-15
    most searched coins — strong leading indicator of retail attention
    and potential pump targets.

    Returns:
        Dict with trending tokens, count, and top picks
    """
    logger.info("coingecko_trending")
    try:
        from app.domains.scanners.social_signals import _check_coingecko_trending as _real_trending

        result = await _real_trending()
        if result:
            return {
                "check": "trending",
                "result": "scanned",
                "trending_count": result.get("trending_count", 0),
                "top_3": result.get("top_3", []),
                "all_trending": result.get("all_trending", []),
                "source": "coingecko_trending",
            }
    except ImportError:
        logger.debug("social_signals_module_not_available")
    except Exception as e:
        logger.debug("coingecko_trending_failed", error=str(e)[:80])

    # Direct fallback if social_signals module is not available
    try:
        api_key = os.getenv("COINGECKO_API_KEY", "")
        headers = {}
        if api_key:
            headers["x-cg-demo-api-key"] = api_key

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/search/trending",
                headers=headers,
            )
            if resp.status_code != 200:
                return {
                    "check": "trending",
                    "result": "unavailable",
                    "error": f"HTTP {resp.status_code}",
                }

            data = resp.json()
            coins = data.get("coins", [])[:15]
            trending = []
            for c in coins:
                item = c.get("item", {})
                trending.append(
                    {
                        "name": item.get("name"),
                        "symbol": item.get("symbol"),
                        "market_cap_rank": item.get("market_cap_rank"),
                        "score": item.get("score"),
                    }
                )

            return {
                "check": "trending",
                "result": "scanned",
                "trending_count": len(trending),
                "top_3": trending[:3],
                "all_trending": trending,
                "source": "coingecko",
            }
    except httpx.TimeoutException:
        logger.debug("coingecko_trending_timeout")
    except Exception as e:
        logger.warning("coingecko_trending_failed", error=str(e)[:80])

    return {"check": "trending", "result": "unavailable", "error": "CoinGecko trending unavailable"}


async def _check_webacy(address: str, chain: str) -> dict[str, Any] | None:
    """Check Webacy security score for address.

    Args:
        address: Wallet or contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Webacy score or unavailable status
    """
    logger.info("webacy_check", address=address, chain=chain)
    return {"check": "webacy", "result": "unavailable", "reason": "Webacy API not configured"}


async def _check_sourcify(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Sourcify contract verification status.

    Uses the free Sourcify API to check if a contract is verified (full or partial match).
    Sourcify has the most comprehensive verification database — better than Etherscan alone.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with verification status, match type (full/partial), compiler version,
        and contract metadata. Returns unavailable if contract not found.
    """
    logger.info("sourcify_check", token=token_address, chain=chain)
    from app.domains.scanner.modules._core import GOPLUS_CHAIN_IDS

    chain_id = GOPLUS_CHAIN_IDS.get(chain, chain)
    if not chain_id or chain_id == chain:
        # Unsupported chain for Sourcify (e.g., Solana)
        return {
            "check": "sourcify",
            "result": "unavailable",
            "reason": f"Chain {chain} not supported by Sourcify",
        }

    try:
        # Full match check
        full_url = f"https://sourcify.dev/server/api/v2/contract/{chain_id}/{token_address}"
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(full_url)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "check": "sourcify",
                    "result": "scanned",
                    "is_verified": True,
                    "match_type": "full",
                    "compiler_version": data.get(
                        "compilerVersion", data.get("compiler_version", "")
                    ),
                    "source": "sourcify",
                    "contract_name": data.get("name", ""),
                    "evm_version": data.get("evmVersion", "") or data.get("evm_version", ""),
                    "optimization": data.get("optimizationUsed", "")
                    or data.get("optimization_used", ""),
                }
            elif resp.status_code == 404:
                # Try partial match
                partial_url = f"https://sourcify.dev/server/api/v2/contract/{chain_id}/{token_address}?matchType=partial"
                resp2 = await client.get(partial_url)
                if resp2.status_code == 200:
                    data = resp2.json()
                    return {
                        "check": "sourcify",
                        "result": "scanned",
                        "is_verified": True,
                        "match_type": "partial",
                        "source": "sourcify",
                        "contract_name": data.get("name", ""),
                    }

                # Try metadata check as final fallback
                metadata_url = f"https://sourcify.dev/server/api/v2/contract/{chain_id}/{token_address}/metadata"
                resp3 = await client.get(metadata_url)
                if resp3.status_code == 200:
                    return {
                        "check": "sourcify",
                        "result": "scanned",
                        "is_verified": True,
                        "match_type": "metadata_only",
                        "source": "sourcify",
                    }

                return {
                    "check": "sourcify",
                    "result": "scanned",
                    "is_verified": False,
                    "match_type": "none",
                    "source": "sourcify",
                }

            return {
                "check": "sourcify",
                "result": "unavailable",
                "error": f"HTTP {resp.status_code}",
            }

    except httpx.TimeoutException:
        logger.debug("sourcify_timeout", chain=chain, token=token_address[:10])
    except Exception as e:
        logger.debug("sourcify_failed", error=str(e)[:80])

    return {"check": "sourcify", "result": "unavailable", "error": "Sourcify API unavailable"}


async def _check_scamsniffer_live(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check ScamSniffer Live for real-time scam alerts.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with ScamSniffer result or unavailable status
    """
    logger.info("scamsniffer_live", token=token_address, chain=chain)
    return {"check": "scamsniffer", "result": "unavailable", "reason": "ScamSniffer not integrated"}


async def _check_dune(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Dune Analytics for on-chain query data.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Dune data or unavailable status
    """
    logger.info("dune_check", token=token_address, chain=chain)
    return {"check": "dune", "result": "unavailable", "reason": "Dune API not configured"}


async def _check_arkham(address: str, chain: str) -> dict[str, Any] | None:
    """Check Arkham Intelligence for address labels.

    Args:
        address: Wallet or contract address
        chain: Blockchain network identifier

    Returns:
        Dict with Arkham labels or unavailable status
    """
    logger.info("arkham_check", address=address, chain=chain)
    return {"check": "arkham", "result": "unavailable", "reason": "Arkham API not configured"}


async def _check_thegraph(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check The Graph subgraph for token data.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with subgraph data or unavailable status
    """
    logger.info("thegraph_check", token=token_address, chain=chain)
    return {"check": "thegraph", "result": "unavailable", "reason": "Subgraph query not deployed"}


# ── Risk scoring engine ───────────────────────────────────────────────


def _calculate_safety_score(modules_results: list[dict[str, Any]]) -> int:
    """Calculate safety score (0-100) from all module results.

    Baseline: 50 (unknown/neutral).
    Each module contributes risk based on its findings:
    - Modules returning error/offline: no impact
    - Clean modules: +5 to score
    - Risk flags: -risk_score contribution
    """
    score = 50  # Neutral baseline
    clean_bonus = 0
    risk_penalty = 0
    modules_with_data = 0

    for mod_entry in modules_results:
        r = mod_entry.get("result", {})
        if not isinstance(r, dict):
            continue
        result_status = r.get("result", "")
        if result_status in ("error", "pending", "unavailable"):
            continue  # Module didn't return useful data

        modules_with_data += 1

        # Check for risk indicators
        risk_score = r.get("risk_score", 0)
        if risk_score and isinstance(risk_score, (int, float)):
            risk_penalty += risk_score * 0.4  # penalty proportional to risk

        # ── Core risk flags ──
        is_honeypot = r.get("is_honeypot", False)
        if is_honeypot:
            risk_penalty += 35

        is_spam = r.get("is_spam", False)
        if is_spam:
            risk_penalty += 30

        is_sanctioned = r.get("is_sanctioned", False)
        if is_sanctioned:
            risk_penalty += 50

        is_bundled = r.get("is_bundled", False)
        if is_bundled:
            confidence = r.get("confidence", 0)
            risk_penalty += confidence * 50

        is_verified = r.get("is_verified", None)
        if is_verified is True:
            clean_bonus += 10
        elif is_verified is False:
            risk_penalty += 10

        # ── Deployer reputation ──
        reputation_label = r.get("reputation_label", "")
        if reputation_label == "CRITICAL":
            risk_penalty += 30
        elif reputation_label == "HIGH":
            risk_penalty += 20
        elif reputation_label == "LOW":
            clean_bonus += 10

        # ── Copycat ──
        copycat_result = r.get("result", "")
        if copycat_result == "high_risk":
            risk_penalty += 25
        elif copycat_result == "medium_risk":
            risk_penalty += 15
        elif copycat_result == "clean":
            clean_bonus += 5

        # ── LP locked ──
        lp_locked = r.get("liquidity_locked_pct", 0)
        if lp_locked and isinstance(lp_locked, (int, float)):
            if lp_locked >= 90:
                clean_bonus += 10
            elif lp_locked <= 10 and r.get("liquidity_usd", 0) > 0:
                risk_penalty += 20

        # ── Ownership renounced (new check) ──
        ownership_renounced = r.get("ownership_renounced", None)
        if ownership_renounced is True:
            clean_bonus += 10
        elif ownership_renounced is False:
            # Active ownership is moderate risk
            risk_level = r.get("risk_level", "")
            if risk_level == "active_proxy_owner":
                risk_penalty += 25
            else:
                risk_penalty += 10

        # ── High concentration (holders) ──
        concentration = r.get("concentration_pct", 0)
        if isinstance(concentration, (int, float)) and concentration > 50:
            risk_penalty += 15

        # ── LP concentration (new check) ──
        top10_pct = r.get("top10_pct", 0)
        if isinstance(top10_pct, (int, float)) and top10_pct > 0:
            if top10_pct >= 90:
                risk_penalty += 30
            elif top10_pct >= 70:
                risk_penalty += 20
            elif top10_pct >= 50:
                risk_penalty += 10

        # ── Social sentiment (new check) ──
        if r.get("is_suspicious"):
            suspicion_count = len(r.get("suspicion_reasons", []))
            risk_penalty += 15 * suspicion_count

        # ── Token age (new check) ──
        age_risk_level = r.get("risk_level", "")
        if age_risk_level == "critical":
            risk_penalty += 20
        elif age_risk_level == "high":
            risk_penalty += 12
        elif age_risk_level == "medium":
            risk_penalty += 5
        elif age_risk_level == "low":
            clean_bonus += 5

        # ── Volume/liquidity ratio (new check) ──
        if r.get("is_wash_trading_suspicious"):
            risk_penalty += 20
        vol_risk_level = r.get("risk_level", "")
        if vol_risk_level == "critical":
            risk_penalty += 25
        elif vol_risk_level == "high":
            risk_penalty += 15

    # Apply modifiers
    score = score + clean_bonus - risk_penalty

    # If we have good data, boost confidence can raise score
    if modules_with_data >= 6 and risk_penalty == 0:
        score = min(95, score + 10)

    # Clamp to 0-100
    return max(0, min(100, int(score)))


async def scan_token(
    token_address: str,
    chain: str = "ethereum",
    tiers: list[str] | None = None,
    include_market_data: bool = True,
    include_social: bool = True,
) -> ScanResult:
    """Scan a token for security issues across ALL SENTINEL modules.

    FREE tier:    SENTINEL Tier 1  (market_data, honeypot, goplus, copycat, spam,
                                    blockscout, defiscanner, token_age, vol/liq ratio)
    PRO tier:     Tier 1 + deployer, holders, verification, liquidity, bundle,
                  lp_concentration, ownership_renounced, social_sentiment
    ELITE tier:   Tier 1+2 + deep deployer, price consensus, RAG, nansen,
                  blowfish, dune, arkham
    """
    if tiers is None:
        tiers = ["free"]

    result = ScanResult(token_address=token_address, chain=chain)

    last_error: str | None = None

    # Run modules in parallel
    async def run_module(name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal last_error
        try:
            r = await func(*args, **kwargs)
            result.modules_run.append({"module": name, "status": "success", "result": r})
            return r
        except Exception as e:
            logger.warning("module_failed", module=name, error=str(e))
            last_error = str(e)
            result.modules_run.append({"module": name, "status": "error", "error": str(e)})
            return None

    tasks = []

    # ── FREE tier (Tier 1) — core checks (no API keys needed) ──
    if "free" in tiers or "pro" in tiers or "elite" in tiers:
        tasks.append(run_module("market_data", fetch_market_data, token_address, chain))
        tasks.append(run_module("honeypot", _check_honeypot_is, token_address, chain))
        tasks.append(run_module("goplus", _check_goplus_security, token_address, chain))
        tasks.append(run_module("copycat", _check_copycat, token_address, ""))
        tasks.append(run_module("spam", _check_spam, token_address, chain))
        tasks.append(run_module("blockscout", _check_blockscout, token_address, chain))
        tasks.append(run_module("defiscanner", _check_defi_scanner, token_address, chain))
        # New FREE checks
        tasks.append(run_module("token_age", _check_token_age, token_address, chain))
        tasks.append(
            run_module(
                "volume_liquidity_ratio", _check_volume_liquidity_ratio, token_address, chain
            )
        )

    # ── PRO tier (Tier 2) — advanced checks ──
    if "pro" in tiers or "elite" in tiers:
        tasks.append(run_module("deployer", _get_deployer_info, token_address, chain))
        tasks.append(run_module("holders", _get_holder_data, token_address, chain))
        tasks.append(run_module("verification", _check_contract_verification, token_address, chain))
        tasks.append(run_module("liquidity", _check_liquidity_lock_multi, token_address, chain))
        tasks.append(run_module("bundle", _check_bundle, token_address, chain))
        # New PRO checks
        tasks.append(run_module("lp_concentration", _check_lp_concentration, token_address, chain))
        tasks.append(run_module("ownership", _check_ownership_renounced, token_address, chain))
        tasks.append(run_module("social_sentiment", _check_social_sentiment, token_address, chain))

    # ── ELITE tier (Tier 3) — deep analysis ──
    if "elite" in tiers:
        tasks.append(run_module("price", _get_price_consensus, token_address, chain))
        tasks.append(run_module("rag", _rag_scam_check, token_address, chain))
        tasks.append(run_module("nansen", _check_nansen, token_address, chain))
        tasks.append(run_module("blowfish", _check_blowfish, token_address, chain))
        tasks.append(run_module("dune", _check_dune, token_address, chain))
        tasks.append(run_module("arkham", _check_arkham, token_address, chain))

    # ── Run all tasks ──
    await asyncio.gather(*tasks)

    # ── Calculate real safety score from module results ──
    result.safety_score = _calculate_safety_score(result.modules_run)

    # ── Generate risk flags ──
    risk_flags: list[str] = []
    for mod_entry in result.modules_run:
        r = mod_entry.get("result")
        if not isinstance(r, dict):
            continue

        module_name = mod_entry.get("module", "")

        if r.get("is_honeypot"):
            risk_flags.append("honeypot")
        if r.get("is_spam"):
            risk_flags.append("flagged_spam")
        if r.get("is_sanctioned"):
            risk_flags.append("sanctioned_address")
        if r.get("is_bundled"):
            risk_flags.append("bundled_launch")
        if r.get("result") == "high_risk" and module_name == "copycat":
            risk_flags.append("copycat_token")
        if r.get("reputation_label") == "CRITICAL":
            risk_flags.append("malicious_deployer")
        if r.get("reputation_label") == "HIGH":
            risk_flags.append("high_risk_deployer")

        risk_score = r.get("risk_score", 0)
        if (
            isinstance(risk_score, (int, float))
            and risk_score >= 70
            and module_name in ("goplus", "honeypot")
        ):
            risk_flags.append("high_risk_score")

        # Concentration (existing)
        if isinstance(r.get("concentration_pct"), (int, float)) and r["concentration_pct"] > 80:
            risk_flags.append("concentrated_holders")

        # ── New check risk flags ──
        # Token age
        if r.get("risk_level") == "critical" and module_name == "token_age":
            risk_flags.append("new_token_under_1h")
        elif r.get("is_new_token") and module_name == "token_age":
            risk_flags.append("token_under_24h")

        # LP concentration
        if module_name == "lp_concentration":
            if r.get("risk_level") == "critical":
                risk_flags.append("extreme_lp_concentration")
            elif r.get("is_concentrated"):
                risk_flags.append("concentrated_lp")

        # Ownership
        if module_name == "ownership":
            if r.get("risk_level") == "active_proxy_owner":
                risk_flags.append("active_proxy_owner")
            elif r.get("risk_level") == "active_owner":
                risk_flags.append("active_owner")

        # Social sentiment
        if r.get("is_suspicious") and module_name == "social_sentiment":
            risk_flags.append("suspicious_social_sentiment")

        # Volume/liquidity ratio
        if r.get("is_wash_trading_suspicious") and module_name == "volume_liquidity_ratio":
            risk_flags.append("wash_trading_suspected")
        if module_name == "volume_liquidity_ratio" and r.get("risk_level") == "critical":
            risk_flags.append("extreme_volume_liquidity_ratio")

    result.risk_flags = risk_flags

    # ── Calculate confidence based on how many modules returned data ──
    successful = sum(1 for m in result.modules_run if m.get("status") == "success")
    total = len(result.modules_run) if result.modules_run else 1
    base_confidence = min(100, int((successful / total) * 100))
    # Add bonus for modules with real data (not pending)
    pending_count = sum(
        1
        for m in result.modules_run
        if isinstance(m.get("result"), dict) and m["result"].get("result") == "pending"
    )
    real_count = successful - pending_count
    result.confidence = min(100, base_confidence + real_count * 5)

    # ── Fuse token risk via DataFusion engine ──
    # Extract module results for the fusion engine to cross-reference
    goplus_result: dict[str, Any] | None = None
    dexscreener_result: dict[str, Any] | None = None
    honeypot_result: dict[str, Any] | None = None
    sentinel_result: dict[str, Any] | None = None

    for mod_entry in result.modules_run:
        r = mod_entry.get("result")
        if not isinstance(r, dict):
            continue
        module_name = mod_entry.get("module", "")
        if module_name == "goplus" and r.get("result") not in ("error", "unavailable"):
            goplus_result = r
        elif module_name == "defiscanner" and r.get("result") not in ("error", "unavailable"):
            dexscreener_result = r
        elif module_name == "honeypot" and r.get("result") not in ("error", "unavailable"):
            honeypot_result = r
        elif module_name == "market_data":
            sentinel_result = r

    # Run fusion — uses weighted voting, outlier removal, freshness decay
    try:
        fused = await fusion.fuse_token_risk(
            goplus_result=goplus_result,
            dexscreener_result=dexscreener_result,
            honeypot_result=honeypot_result,
            sentinel_result=sentinel_result,
        )
        result.fusion_brief = fusion.generate_intelligence_brief(fused)
        # If fusion confidence is high, adjust safety_score to match
        if fused.get("confidence", 0) > 0.5 and fused.get("fused_value") is not None:
            fused_risk = fused["fused_value"]
            # Convert risk score (0-100, higher=riskier) to safety score (0-100, higher=safer)
            fused_safety = max(0, min(100, 100 - fused_risk))
            # Blend: 70% original scoring, 30% fusion (increase fusion weight with more sources)
            fusion_weight = min(0.5, 0.2 + len(fused.get("sources_used", [])) * 0.05)
            result.safety_score = int(
                result.safety_score * (1 - fusion_weight) + fused_safety * fusion_weight
            )
            # Also apply high-severity findings as risk flags
            for finding in fused.get("risk_findings", []):
                if finding.get("severity") in ("CRITICAL", "HIGH"):
                    flag_text = finding.get("finding", "")[:60]
                    if flag_text and flag_text not in result.risk_flags:
                        result.risk_flags.append(
                            f"fusion:{finding.get('source', 'unknown')}:{flag_text}"
                        )
    except Exception as e:
        logger.warning("data_fusion_skipped", error=str(e)[:100])
        result.fusion_brief = {"summary": "Fusion engine unavailable", "risk_score": None}

    # Ingest to RAG
    await _ingest_scan_to_rag(result)

    return result


async def quick_scan_text(token_address: str, chain: str = "solana") -> str:
    """Quick text scan for token."""
    result = await scan_token(token_address, chain, tiers=["free", "pro"])
    return (
        f"Token {token_address} scanned on {chain}. "
        f"Safety score: {result.safety_score}/100. "
        f"Confidence: {result.confidence}%. "
        f"Flags: {', '.join(result.risk_flags) if result.risk_flags else 'None'}"
    )


# Backward compatibility: re-export scan_token
from app.domains.scanner.service import scan_token  # noqa: E402, F811
