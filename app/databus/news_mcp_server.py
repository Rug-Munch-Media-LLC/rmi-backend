"""
RMI News MCP Server — Expose our massive free crypto news aggregation
to AI agents, Claude, Cursor, and any MCP-compatible client.

50+ sources, 1500+ articles, real-time updates every 5 minutes.
Totally free. No API key needed. Built for the people.
"""

import json

from fastmcp import FastMCP

from app.core.redis import get_redis

mcp = FastMCP(
    "rmi-news", description="RMI Free Crypto News — 50+ sources, real-time, no API key needed"
)


# Redis connection helper
def search_news(query: str, limit: int = 10) -> dict:
    """Search crypto news articles by keyword. Returns title, source, sentiment, and URL."""
    r = get_redis()
    ids = r.zrevrange("rmi:news:index", 0, -1)
    results = []
    q = query.lower()
    for aid in ids:
        article = r.get(f"rmi:news:article:{aid}")
        if article:
            a = json.loads(article)
            if q in a["title"].lower() or q in a.get("content", "").lower():
                results.append(
                    {
                        "title": a["title"],
                        "source": a["source"],
                        "sentiment": a["sentiment"],
                        "url": a.get("url", ""),
                    }
                )
                if len(results) >= limit:
                    break
    # Also search social
    sids = r.zrevrange("rmi:news:social:index", 0, -1)
    for sid in sids:
        article = r.get(f"rmi:news:article:{sid}")
        if article:
            a = json.loads(article)
            if q in a["title"].lower() or q in a.get("content", "").lower():
                results.append(
                    {
                        "title": a["title"],
                        "source": a["source"],
                        "sentiment": a.get("sentiment", 0),
                        "url": a.get("url", ""),
                    }
                )
                if len(results) >= limit + 5:
                    break

    return {"query": query, "count": len(results[:limit]), "results": results[:limit]}


@mcp.tool()
def get_latest_news(limit: int = 20, include_social: bool = True) -> dict:
    """Get the latest crypto news articles across all sources."""
    r = get_redis()
    results = []
    ids = r.zrevrange("rmi:news:index", 0, limit - 1)
    for aid in ids:
        article = r.get(f"rmi:news:article:{aid}")
        if article:
            a = json.loads(article)
            results.append(
                {
                    "title": a["title"],
                    "source": a["source"],
                    "sentiment": a["sentiment"],
                    "url": a.get("url", ""),
                    "tickers": a.get("tickers", []),
                }
            )

    if include_social:
        sids = r.zrevrange("rmi:news:social:index", 0, min(limit // 2, 50))
        for sid in sids:
            article = r.get(f"rmi:news:article:{sid}")
            if article:
                a = json.loads(article)
                results.append(
                    {
                        "title": a["title"],
                        "source": a["source"],
                        "sentiment": a.get("sentiment", 0),
                        "url": a.get("url", ""),
                    }
                )

    return {"count": len(results), "results": results}


@mcp.tool()
def get_news_by_source(source: str, limit: int = 20) -> dict:
    """Get news articles filtered by source name (e.g. cointelegraph, decrypt, coindesk)."""
    r = get_redis()
    results = []
    ids = r.zrevrange("rmi:news:index", 0, -1)
    for aid in ids:
        article = r.get(f"rmi:news:article:{aid}")
        if article:
            a = json.loads(article)
            if a["source"] == source:
                results.append(
                    {"title": a["title"], "sentiment": a["sentiment"], "url": a.get("url", "")}
                )
                if len(results) >= limit:
                    break
    return {"source": source, "count": len(results), "results": results}


@mcp.tool()
def get_news_by_sentiment(min_sentiment: float = 0.3, limit: int = 20) -> dict:
    """Get news articles filtered by minimum sentiment score (0 to 1, positive = bullish)."""
    r = get_redis()
    bullish, bearish = [], []
    ids = r.zrevrange("rmi:news:index", 0, -1)
    for aid in ids:
        article = r.get(f"rmi:news:article:{aid}")
        if article:
            a = json.loads(article)
            if a["sentiment"] >= min_sentiment:
                bullish.append(
                    {"title": a["title"], "source": a["source"], "sentiment": a["sentiment"]}
                )
            elif a["sentiment"] <= -min_sentiment:
                bearish.append(
                    {"title": a["title"], "source": a["source"], "sentiment": a["sentiment"]}
                )
    return {
        "bullish_count": len(bullish),
        "bearish_count": len(bearish),
        "bullish": bullish[:limit],
        "bearish": bearish[:limit],
    }


@mcp.tool()
def get_trending_tickers(limit: int = 10) -> dict:
    """Get the most mentioned crypto tickers across all news sources."""
    r = get_redis()
    ticker_count = {}
    ids = r.zrevrange("rmi:news:index", 0, -1)
    for aid in ids:
        article = r.get(f"rmi:news:article:{aid}")
        if article:
            for t in json.loads(article).get("tickers", []):
                ticker_count[t] = ticker_count.get(t, 0) + 1
    trending = sorted(ticker_count.items(), key=lambda x: x[1], reverse=True)[:limit]
    return {"count": len(trending), "trending": [{"ticker": t, "mentions": c} for t, c in trending]}


@mcp.tool()
def get_news_stats() -> dict:
    """Get aggregated statistics about the RMI news corpus."""
    r = get_redis()
    stats = json.loads(r.get("rmi:news:stats") or "{}")
    social = r.zcard("rmi:news:social:index")
    return {
        "total_articles": stats.get("total_fetched", 0) + social,
        "sources": stats.get("sources_successful", 0),
        "sentiment_avg": stats.get("sentiment_avg", 0),
        "social_posts": social,
        "last_update": stats.get("last_ingest", 0),
        "free": True,
        "no_api_key": True,
        "powered_by": "RMI — Rug Munch Intelligence",
    }


@mcp.resource("news://latest")
def news_latest_resource() -> str:
    """Get latest news as a text resource."""
    r = get_redis()
    results = []
    ids = r.zrevrange("rmi:news:index", 0, 9)
    for aid in ids:
        article = r.get(f"rmi:news:article:{aid}")
        if article:
            a = json.loads(article)
            sent = "🟢" if a["sentiment"] > 0.1 else ("🔴" if a["sentiment"] < -0.1 else "⚪")
            results.append(f"{sent} [{a['source']}] {a['title']}")
    return "\n".join(results)


if __name__ == "__main__":
    mcp.run(transport="stdio")
