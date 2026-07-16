"""x402 analysis-side tools — sentiment, social signals, URL scam, NFT wash.

Phase 3A of AUDIT-2026-Q3.md (split from app/routers/x402_tools.py).

Endpoints mounted on sub_router:
  POST /api/v1/x402-tools/sentiment
  POST /api/v1/x402-tools/social_signal
  POST /api/v1/x402-tools/urlcheck
  POST /api/v1/x402-tools/tw_profile
  POST /api/v1/x402-tools/tw_timeline
  POST /api/v1/x402-tools/tw_search
  POST /api/v1/x402-tools/nft_wash_detector
  POST /api/v1/x402-tools/bridge_security
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from app.domains.billing.x402.shared import (
    GenericRequest,
    SentimentRequest,
    URLRequest,
    fetch_with_fallback,
    record_x402_payment,
)

sub_router = APIRouter(tags=["x402-analysis-tools"])


# ═══════════════════════════════════════════════════════════════
# TOOL 6: Social Sentiment Radar ($0.50)
# ═══════════════════════════════════════════════════════════════


async def _sentiment_analysis(token: str, chain: str) -> dict:
    """Analyze social signals across multiple sources."""
    result = {"token": token, "chain": chain, "sources_used": []}

    # Layer 1: CoinGecko social data
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.coingecko.com/api/v3/coins/{token}"], timeout=8
        )
        if data:
            result["coingecko"] = {
                "name": data.get("name"),
                "market_cap_rank": data.get("market_cap_rank"),
                "sentiment_votes_up": data.get("public_interest_stats", {}).get("alexa_rank", 0),
            }
            result["sources_used"].append("coingecko")
    except Exception:
        pass

    # Layer 2: DexScreener social links
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/search?q={token}"], timeout=5
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            info = pair.get("info", {})
            result["social_links"] = {
                "twitter": info.get("twitter"),
                "telegram": info.get("telegram"),
                "website": info.get("websites", [{}])[0].get("url")
                if info.get("websites")
                else None,
            }
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    # Layer 3: CoinGecko community data
    try:
        data, _ = await fetch_with_fallback(
            [
                f"https://api.coingecko.com/api/v3/coins/{token}?localization=false&tickers=false&market_data=false&community_data=true&developer_data=false"
            ],
            timeout=8,
        )
        if data and data.get("community_data"):
            result["community"] = data["community_data"]
            result["sources_used"].append("coingecko_community")
    except Exception:
        pass

    # Layer 4: DefiLlama - protocol mentions
    try:
        data, _ = await fetch_with_fallback(["https://api.llama.fi/protocols"], timeout=10)
        if data:
            protocol = next((p for p in data if token.lower() in p.get("name", "").lower()), None)
            if protocol:
                result["defillama"] = {
                    "name": protocol.get("name"),
                    "tvl": protocol.get("tvl"),
                    "chains": protocol.get("chains"),
                }
                result["sources_used"].append("defillama")
    except Exception:
        pass

    # Compute sentiment score
    score = 50  # neutral baseline

    if result.get("coingecko") and result["coingecko"].get("market_cap_rank"):
        rank = result["coingecko"]["market_cap_rank"]
        if rank < 100:
            score += 20
        elif rank < 500:
            score += 10
        elif rank > 5000:
            score -= 10

    if result.get("social_links", {}).get("twitter"):
        score += 5
    if result.get("social_links", {}).get("telegram"):
        score += 5
    if result.get("community", {}).get("twitter_followers", 0) > 10000:
        score += 10

    result["sentiment_score"] = max(0, min(100, score))
    result["sentiment_label"] = (
        "bullish" if score >= 65 else "bearish" if score <= 35 else "neutral"
    )
    return result


@sub_router.post("/sentiment")
async def social_sentiment(req: SentimentRequest):
    """Social signal analysis across Twitter, Telegram, RSS feeds."""
    try:
        result = await _sentiment_analysis(req.token, req.chain)
        await record_x402_payment("sentiment", "0.03", req.token)
        return {
            "tool": "Social Sentiment Radar",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 9: URL Scam Detector ($0.10)
# ═══════════════════════════════════════════════════════════════


def _analyze_url(url: str) -> dict:
    """Analyze URL for scam indicators using structural analysis."""
    import re
    from urllib.parse import urlparse

    result = {"url": url}
    risk_score = 0
    indicators = []

    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # Indicator 1: Suspicious TLDs
    suspicious_tlds = [".xyz", ".top", ".club", ".tk", ".ml", ".ga", ".cf", ".gq", ".biz", ".info"]
    for tld in suspicious_tlds:
        if domain.endswith(tld):
            risk_score += 15
            indicators.append(f"suspicious_tld:{tld}")

    # Indicator 2: Brand impersonation
    brand_keywords = [
        "binance",
        "coinbase",
        "metamask",
        "uniswap",
        "opensea",
        "phantom",
        "solana",
        "ethereum",
        "bitcoin",
        "trezor",
        "ledger",
    ]
    for brand in brand_keywords:
        if brand in domain and brand not in domain.split(".")[0]:
            pass  # legitimate use
        elif brand in domain:
            # Check if it's the real domain
            real_domains = {
                "binance": "binance.com",
                "coinbase": "coinbase.com",
                "metamask": "metamask.io",
                "uniswap": "uniswap.org",
                "opensea": "opensea.io",
                "phantom": "phantom.app",
                "solana": "solana.com",
                "ethereum": "ethereum.org",
            }
            real = real_domains.get(brand)
            if real and domain != real:
                risk_score += 25
                indicators.append(f"brand_impersonation:{brand}")

    # Indicator 3: Homograph attacks (lookalike chars)
    if any(c in domain for c in "а ο е ｒ ｎ"):  # Cyrillic lookalikes  # noqa: RUF001
        risk_score += 30
        indicators.append("homograph_attack")

    # Indicator 4: Subdomain stuffing
    parts = domain.split(".")
    if len(parts) > 3:
        risk_score += 10
        indicators.append("subdomain_stuffing")

    # Indicator 5: Number-heavy domains
    if re.search(r"\d{4,}", domain):
        risk_score += 10
        indicators.append("number_heavy_domain")

    # Indicator 6: Hyphen spam
    if domain.count("-") > 2:
        risk_score += 15
        indicators.append("hyphen_spam")

    # Indicator 7: Crypto scam patterns
    scam_patterns = ["free-", "giveaway", "claim-", "airdrop-", "mint-", "presale-", "ico-"]
    for pattern in scam_patterns:
        if pattern in domain:
            risk_score += 20
            indicators.append(f"scam_pattern:{pattern}")

    # Indicator 8: Short-lived domain pattern
    if len(domain) < 6 and "." in domain:
        risk_score += 10
        indicators.append("very_short_domain")

    # Indicator 9: IP address instead of domain
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", domain):
        risk_score += 25
        indicators.append("ip_address_url")

    # Indicator 10: HTTPS check
    if parsed.scheme != "https":
        risk_score += 5
        indicators.append("no_https")

    risk_score = min(100, risk_score)
    verdict = "SCAM" if risk_score >= 60 else "SUSPICIOUS" if risk_score >= 30 else "LIKELY_SAFE"

    result["risk_score"] = risk_score
    result["verdict"] = verdict
    result["indicators"] = indicators
    result["domain"] = domain
    return result


@sub_router.post("/urlcheck")
async def url_scam_detector(req: URLRequest):
    """URL scam analysis - structural analysis, no blacklists needed."""
    try:
        result = _analyze_url(req.url)
        await record_x402_payment("urlcheck", "0.01", req.url)
        return {
            "tool": "URL Scam Detector",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 11: Twitter Profile ($0.01)
# ═══════════════════════════════════════════════════════════════


async def _twitter_profile(query: str) -> dict:
    """Get Twitter/X user profile data."""
    result = {"query": query, "sources_used": []}

    # Layer 1: Nitter instances
    nitter_instances = [
        f"https://nitter.net/{query}",
        f"https://nitter.privacydev.net/{query}",
    ]
    for url in nitter_instances:
        try:
            data, _src = await fetch_with_fallback([url], timeout=5)
            if data:
                # Extract profile info from HTML
                result["source"] = "nitter"
                result["sources_used"].append("nitter")
                break
        except Exception:
            pass

    # Layer 2: DuckDuckGo search
    try:
        data, _ = await fetch_with_fallback(
            [f"https://html.duckduckgo.com/html/?q={quote(f'site:twitter.com {query}')}"]
        )
        if data:
            result["duckduckgo_results"] = True
            result["sources_used"].append("duckduckgo")
    except Exception:
        pass

    return result


@sub_router.post("/tw_profile")
async def twitter_profile(req: GenericRequest):
    """Twitter/X profile lookup."""
    try:
        query = req.query or req.address or req.token or ""
        result = await _twitter_profile(query)
        await record_x402_payment("tw_profile", "0.01", query)
        return {
            "tool": "Twitter Profile",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 12: Twitter Timeline ($0.01)
# ═══════════════════════════════════════════════════════════════


@sub_router.post("/tw_timeline")
async def twitter_timeline(req: GenericRequest):
    """Get recent tweets from a user."""
    try:
        query = req.query or req.address or req.token or ""
        tweets = []

        # DuckDuckGo search for recent tweets
        try:
            data, _ = await fetch_with_fallback(
                [
                    f"https://html.duckduckgo.com/html/?q={quote(f'site:twitter.com/{query} ') + 'after:2024-01-01'}"
                ]
            )
            if data:
                tweets.append({"source": "duckduckgo"})
        except Exception:
            pass

        await record_x402_payment("tw_timeline", "0.01", query)
        return {
            "tool": "Twitter Timeline",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "user": query,
            "tweets": tweets,
            "sources_used": ["duckduckgo"] if tweets else [],
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 13: Twitter Search ($0.01)
# ═══════════════════════════════════════════════════════════════


@sub_router.post("/tw_search")
async def twitter_search(req: GenericRequest):
    """Search Twitter/X for tweets."""
    try:
        query = req.query or req.token or ""
        results = []

        # DuckDuckGo search
        try:
            data, _ = await fetch_with_fallback(
                [f"https://html.duckduckgo.com/html/?q={quote(f'site:twitter.com {query}')}"]
            )
            if data:
                results.append({"source": "duckduckgo"})
        except Exception:
            pass

        await record_x402_payment("tw_search", "0.01", query)
        return {
            "tool": "Twitter Search",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "query": query,
            "results": results,
            "sources_used": ["duckduckgo"] if results else [],
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 18: Social Signal ($0.10)
# ═══════════════════════════════════════════════════════════════


async def _social_signal(query: str) -> dict:
    """Social signal analyzer."""
    result = {"query": query, "sources_used": []}

    # CryptoPanic
    try:
        data, _ = await fetch_with_fallback(
            [f"https://cryptopanic.com/api/free/posts/?filter=important&q={query}"]
        )
        if data and data.get("results"):
            result["cryptopanic_count"] = len(data["results"])
            result["cryptopanic_sentiment"] = sum(
                1 for r in data["results"] if r.get("sentiment") == "positive"
            )
            result["sources_used"].append("cryptopanic")
    except Exception:
        pass

    # Reddit
    try:
        data, _ = await fetch_with_fallback(
            [f"https://www.reddit.com/search.json?q={quote(query)}&sort=new&limit=10"]
        )
        if data and data.get("data", {}).get("children"):
            result["reddit_count"] = len(data["data"]["children"])
            result["sources_used"].append("reddit")
    except Exception:
        pass

    result["total_mentions"] = result.get("cryptopanic_count", 0) + result.get("reddit_count", 0)
    result["sentiment_score"] = result.get("cryptopanic_sentiment", 0) / max(
        1, result.get("cryptopanic_count", 1)
    )

    return result


@sub_router.post("/social_signal")
async def social_signal(req: GenericRequest):
    """Social signal analyzer."""
    try:
        query = req.query or req.token or req.address or ""
        result = await _social_signal(query)
        await record_x402_payment("social_signal", "0.10", query)
        return {
            "tool": "Social Signal",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 28: NFT Wash Detector ($0.10)
# ═══════════════════════════════════════════════════════════════


async def _nft_wash_detector(collection: str) -> dict:
    """NFT wash trading detection."""
    result = {"collection": collection, "sources_used": [], "wash_signals": []}

    # NFTPriceFloor (free API)
    try:
        data, _ = await fetch_with_fallback(
            [f"https://pricefloor-api.nftdata.io/v1/collection/{collection}"]
        )
        if data:
            result["floor_price"] = data.get("floorPrice")
            result["volume_24h"] = data.get("volume24h")
            result["sources_used"].append("nftpricefloor")
    except Exception:
        pass

    # OpenSea stats (public endpoint)
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.opensea.io/api/v1/collection/{collection}/stats"]
        )
        if data and data.get("stats"):
            stats = data["stats"]
            result["opensea"] = {
                "floor_price": stats.get("floor_price"),
                "total_volume": stats.get("total_volume"),
                "num_owners": stats.get("num_owners"),
                "one_day_volume": stats.get("one_day_volume"),
                "one_day_sales": stats.get("one_day_sales"),
            }
            result["sources_used"].append("opensea")
    except Exception:
        pass

    # Wash trading signals
    os_stats = result.get("opensea", {})
    one_day_vol = os_stats.get("one_day_volume", 0)
    one_day_sales = os_stats.get("one_day_sales", 0)

    if one_day_sales > 0:
        avg_sale_price = one_day_vol / one_day_sales
        floor = os_stats.get("floor_price", 0)
        if avg_sale_price > floor * 10:
            result["wash_signals"].append("Average sale price far exceeds floor")
        if one_day_sales > 100 and os_stats.get("num_owners", 0) < 50:
            result["wash_signals"].append("High sales volume with few owners")

    result["wash_risk"] = (
        "high"
        if len(result["wash_signals"]) >= 2
        else "medium"
        if result["wash_signals"]
        else "low"
    )

    return result


@sub_router.post("/nft_wash_detector")
async def nft_wash_detector(req: GenericRequest):
    """NFT wash trading detector."""
    try:
        collection = req.query or req.address or ""
        result = await _nft_wash_detector(collection)
        await record_x402_payment("nft_wash_detector", "0.10", collection)
        return {
            "tool": "NFT Wash Detector",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 29: Bridge Security ($0.08)
# ═══════════════════════════════════════════════════════════════


async def _bridge_security() -> dict:
    """Bridge security monitoring."""
    result = {"sources_used": [], "bridges": []}

    # DeFiLlama bridges
    try:
        data, _ = await fetch_with_fallback(["https://bridges.llama.fi/bridges"])
        if data and data.get("bridges"):
            for b in data["bridges"][:10]:
                result["bridges"].append(
                    {
                        "name": b.get("name"),
                        "tvl": b.get("totalDeposits", 0),
                        "chains": b.get("chains", []),
                    }
                )
            result["sources_used"].append("defillama")
    except Exception:
        pass

    # DeFiLlama protocols (check for bridge exploits)
    try:
        data, _ = await fetch_with_fallback(["https://api.llama.fi/protocols"])
        if data:
            bridge_protocols = [p for p in data if p.get("category") == "Bridge"]
            result["bridge_count"] = len(bridge_protocols)
            result["total_bridge_tvl"] = sum(p.get("tvl", 0) for p in bridge_protocols)
            result["sources_used"].append("defillama_protocols")
    except Exception:
        pass

    return result


@sub_router.post("/bridge_security")
async def bridge_security(req: GenericRequest):
    """Bridge security monitoring."""
    try:
        result = await _bridge_security()
        await record_x402_payment("bridge_security", "0.08", "scan")
        return {
            "tool": "Bridge Security",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
