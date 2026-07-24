"""Scanner service — external API check functions."""

from __future__ import annotations
import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

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
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("fng_failed", error=str(e)[:100])

    return {"check": "fear_greed", "result": "unavailable", "error": "Fear & Greed API unavailable"}

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
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
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
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("coingecko_trending_failed", error=str(e)[:80])

    return {"check": "trending", "result": "unavailable", "error": "CoinGecko trending unavailable"}

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
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("sourcify_failed", error=str(e)[:80])

    return {"check": "sourcify", "result": "unavailable", "error": "Sourcify API unavailable"}

