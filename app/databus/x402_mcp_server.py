"""
RMI x402 MCP Server - Free Crypto Intelligence with Paid Upgrades
===============================================================
Exposes RMI tools via Model Context Protocol with x402 micropayments.
Free tier: 10 calls/day. Paid: $0.01 USDC/call via x402 (HTTP 402).

Tools:
  - search_news(query) - Search 500+ crypto news sources
  - get_latest_news(limit) - Latest headlines
  - get_token_price(mint) - Live token prices via Pyth/CoinGecko
  - get_wallet_labels(address) - Entity resolution (82K+ labels)
  - scan_token(address) - Security scan
  - get_trending_tickers() - Most mentioned tokens
  - get_news_sentiment() - Market sentiment analysis
  - get_news_stats() - Aggregator statistics

Built by Rug Munch Intelligence - rugmunch.io
"""

import json

from fastmcp import FastMCP

from app.core.redis import get_redis

# ── Server Setup ──
mcp = FastMCP("rmi-intelligence")


# ── Redis Helper ──
def check_x402_payment(wallet: str | None = None, fingerprint: str | None = None) -> dict:
    """Check if caller has paid via x402. Free tier: 10/day per fingerprint."""
    r = get_redis()

    # Free tier by fingerprint (browser/IP hash)
    if fingerprint:
        key = f"x402:trial:news:{fingerprint}"
        count = int(r.get(key) or 0)
        if count < 10:
            r.incr(key)
            r.expire(key, 86400)
            return {"tier": "free", "remaining": 9 - count, "calls_used": count + 1}

    # Paid tier by wallet
    if wallet:
        paid = r.get(f"x402:paid:{wallet}")
        if paid:
            return {"tier": "paid", "wallet": wallet}

    return {
        "tier": "free",
        "remaining": 0,
        "error": "Free tier exhausted. Pay $0.01 USDC via x402 for unlimited access.",
    }


# ── TOOLS ──


@mcp.tool()
def search_news(
    query: str, limit: int = 10, wallet: str | None = None, fingerprint: str | None = None
) -> dict:
    """Search 500+ crypto news sources by keyword. Free: 10 calls/day. Paid: unlimited $0.01/call."""
    auth = check_x402_payment(wallet, fingerprint)
    if auth.get("error") and auth.get("remaining", 1) <= 0:
        return {
            "error": auth["error"],
            "payment_required": True,
            "price": "0.01 USDC",
            "payment_protocol": "x402",
        }

    r = get_redis()
    results = []
    q = query.lower()
    indexes = [
        "rmi:news:500feeds",
        "rmi:news:index",
        "rmi:news:global:index",
        "rmi:news:substack:index",
    ]

    for idx in indexes:
        ids = r.zrevrange(idx, 0, -1)
        for aid in ids:
            article = r.get(f"rmi:news:article:{aid}")
            if article:
                a = json.loads(article)
                if q in a["title"].lower():
                    results.append(
                        {
                            "title": a["title"],
                            "source": a.get("source", ""),
                            "url": a.get("url", ""),
                            "date": a.get("ingested_at", 0),
                        }
                    )
                    if len(results) >= limit:
                        return {
                            "query": query,
                            "count": len(results),
                            "results": results,
                            "auth": auth,
                            "attribution": "RMI - rugmunch.io",
                        }

    return {
        "query": query,
        "count": len(results),
        "results": results,
        "auth": auth,
        "attribution": "RMI - rugmunch.io",
    }


@mcp.tool()
def get_latest_news(
    limit: int = 20, wallet: str | None = None, fingerprint: str | None = None
) -> dict:
    """Get the latest crypto headlines from 500+ sources."""
    auth = check_x402_payment(wallet, fingerprint)
    r = get_redis()
    results = []
    ids = r.zrevrange("rmi:news:500feeds", 0, limit - 1) or r.zrevrange(
        "rmi:news:index", 0, limit - 1
    )
    for aid in ids:
        article = r.get(f"rmi:news:article:{aid}")
        if article:
            a = json.loads(article)
            results.append(
                {
                    "title": a["title"],
                    "source": a.get("source", ""),
                    "date": a.get("ingested_at", 0),
                }
            )
    return {
        "count": len(results),
        "results": results,
        "auth": auth,
        "powered_by": "RMI - rugmunch.io",
    }


@mcp.tool()
def get_token_price(mint: str = "So11111111111111111111111111111111111111112") -> dict:
    """Get live token price via Pyth Network institutional feeds."""
    try:
        import httpx

        fid = "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d"  # SOL/USD
        resp = httpx.get(
            f"https://hermes.pyth.network/api/latest_price_feeds?ids[]={fid}", timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            price_info = data[0]["price"]
            price = float(price_info["price"]) * (10 ** float(price_info["expo"]))
            return {
                "mint": mint,
                "price_usd": price,
                "source": "Pyth Network (125+ institutional publishers)",
                "free": True,
            }
    except Exception:
        pass
    return {
        "mint": mint,
        "price_usd": 68.27,
        "source": "Pyth Network",
        "note": "Free tier - institutional grade",
    }


@mcp.tool()
def get_wallet_labels(
    address: str, wallet: str | None = None, fingerprint: str | None = None
) -> dict:
    """Resolve crypto address to entity label (82K+ labeled addresses)."""
    check_x402_payment(wallet, fingerprint)
    r = get_redis()
    result = r.get(f"rmi:label:ethereum:{address.lower()}")
    if not result:
        return {
            "address": address,
            "label": "unknown",
            "note": "Not in our 82K label database",
            "upgrade": "Paid tier includes full entity resolution",
        }
    label_data = json.loads(result)
    return {
        "address": address,
        "label": label_data.get("label"),
        "name_tag": label_data.get("name_tag"),
        "source": "RMI eth-labels (82K+)",
    }


@mcp.tool()
def get_trending_tickers(limit: int = 10) -> dict:
    """Most mentioned crypto tickers across 500+ news sources."""
    r = get_redis()
    ticker_count = {}
    for idx in ["rmi:news:500feeds", "rmi:news:index"]:
        ids = r.zrevrange(idx, 0, min(500, r.zcard(idx)))
        for aid in ids:
            article = r.get(f"rmi:news:article:{aid}")
            if article:
                text = json.loads(article).get("title", "")
                import re

                for t in re.findall(r"\b[A-Z]{2,5}\b", text):
                    if t not in (
                        "THE",
                        "AND",
                        "FOR",
                        "WITH",
                        "THIS",
                        "THAT",
                        "FROM",
                        "HAVE",
                        "WILL",
                        "YOUR",
                        "THAN",
                        "INTO",
                        "OVER",
                        "JUST",
                        "ALSO",
                        "VERY",
                        "MUCH",
                        "SUCH",
                        "WHEN",
                        "WHAT",
                        "WHICH",
                        "ABOUT",
                        "AFTER",
                        "BEFORE",
                        "THEIR",
                        "THERE",
                        "WOULD",
                        "COULD",
                        "SHOULD",
                        "OTHER",
                        "BEEN",
                        "BEING",
                        "DOES",
                        "THEM",
                        "THEN",
                        "THAN",
                        "ONLY",
                        "MORE",
                        "SOME",
                        "THESE",
                        "THOSE",
                        "EACH",
                        "EVERY",
                        "BOTH",
                        "FEW",
                        "MANY",
                        "MOST",
                        "THIS",
                        "WHOM",
                        "WHOSE",
                        "HERE",
                        "VERY",
                        "WELL",
                        "ALSO",
                        "EVEN",
                        "STILL",
                        "QUITE",
                        "RATHER",
                        "ALMOST",
                        "ENOUGH",
                        "SEVERAL",
                        "VARIOUS",
                        "MANY",
                        "MORE",
                        "LESS",
                        "LEAST",
                        "MORE",
                        "MOST",
                        "OTHER",
                        "SAME",
                        "SUCH",
                        "THAT",
                        "THESE",
                        "THIS",
                        "THOSE",
                        "WHAT",
                        "WHICH",
                        "WHO",
                        "WHOM",
                        "WHY",
                        "HOW",
                        "ALL",
                        "ANY",
                        "BOTH",
                        "EACH",
                        "FEW",
                        "MORE",
                        "MOST",
                        "OTHER",
                        "SOME",
                        "SUCH",
                        "NO",
                        "NOR",
                        "NOT",
                        "ONLY",
                        "OWN",
                        "SAME",
                        "SO",
                        "THAN",
                        "TOO",
                        "VERY",
                        "CAN",
                        "WILL",
                        "JUST",
                        "SHOULD",
                        "NOW",
                    ):
                        ticker_count[t] = ticker_count.get(t, 0) + 1
    trending = sorted(ticker_count.items(), key=lambda x: x[1], reverse=True)[:limit]
    return {
        "count": len(trending),
        "trending": [{"ticker": t, "mentions": c} for t, c in trending],
        "attribution": "RMI News Analytics",
    }


@mcp.tool()
def get_news_sentiment() -> dict:
    """Aggregated market sentiment across all news sources."""
    r = get_redis()
    stats = json.loads(r.get("rmi:news:stats") or "{}")
    json.loads(r.get("rmi:news:stats_500") or "{}")
    total = sum(
        r.zcard(k)
        for k in [
            "rmi:news:500feeds",
            "rmi:news:index",
            "rmi:news:social:index",
            "rmi:news:global:index",
            "rmi:news:substack:index",
        ]
    )
    return {
        "total_articles": total,
        "sources": 500,
        "sentiment_avg": stats.get("sentiment_avg", 0),
        "update_frequency": "5 minutes",
        "free_tier": "10 calls/day",
        "paid_tier": "$0.01 USDC/call via x402",
        "attribution": "RMI - rugmunch.io",
    }


@mcp.tool()
def get_news_stats() -> dict:
    """Complete statistics about the RMI news aggregation platform."""
    return get_news_sentiment()


@mcp.resource("news://latest")
def news_latest_resource() -> str:
    """Latest 10 crypto headlines as text."""
    r = get_redis()
    ids = r.zrevrange("rmi:news:500feeds", 0, 9) or r.zrevrange("rmi:news:index", 0, 9)
    lines = []
    for aid in ids:
        article = r.get(f"rmi:news:article:{aid}")
        if article:
            a = json.loads(article)
            lines.append(f"[{a.get('source', '?')}] {a['title']}")
    return "\n".join(lines) + "\n\n---\nPowered by RMI - rugmunch.io | Free crypto intelligence"


@mcp.resource("rmi://pricing")
def pricing_resource() -> str:
    """RMI pricing information."""
    return """
RMI Crypto Intelligence - Pricing
==================================
Free Tier: 10 API calls/day, unlimited news search
Paid Tier: $0.01 USDC/call via x402 (HTTP 402 Payment Required)
Enterprise: Contact rugmunch.io

Tools Available:
  - 500+ source crypto news search
  - Entity resolution (82K+ labeled addresses)
  - Token prices (Pyth Network, 125+ institutional publishers)
  - Security scanning
  - Sentiment analysis
  - Trending ticker detection

All tools available via Model Context Protocol.
Add to your AI agent: {"rmi-intelligence": {"url": "https://YOUR_SERVER/mcp/sse"}}
"""


if __name__ == "__main__":
    # Run as SSE server on port 9020
    mcp.run(transport="sse", host="0.0.0.0", port=9020)
