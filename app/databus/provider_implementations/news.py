"""News, Social, and Security scan providers."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


async def _messari_news(limit: int = 20, **kw) -> dict[str, Any] | None:
    """Messari News API - curated crypto news with per-asset sentiment scores."""
    api_key = kw.get("api_key", "") or os.getenv("MESSARI_API_KEY", "")
    if not api_key:
        return None
    try:
        headers = {"X-Messari-API-Key": api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.messari.io/news/v1/news/feed", params={"limit": limit}, headers=headers)
            if r.status_code == 200:
                data = r.json()
                if data.get("data"):
                    articles = []
                    for item in data["data"]:
                        assets = [a.get("symbol", "Unknown") for a in item.get("assets", [])]
                        sentiment = item.get("sentiment", [])
                        avg_sentiment = sum(s.get("sentiment", 0) for s in sentiment) / max(len(sentiment), 1)
                        articles.append(
                            {
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "source": item.get("source", {}).get("sourceName", "Messari"),
                                "published_at": item.get("publishTime", ""),
                                "description": item.get("description", ""),
                                "assets": assets,
                                "sentiment_score": avg_sentiment,
                                "category": item.get("category", "news"),
                            }
                        )
                    return {"articles": articles, "source": "messari", "count": len(articles)}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _coindesk_news(limit: int = 20, **kw) -> dict[str, Any] | None:
    """CoinDesk News API - institutional-grade crypto news with categorization."""
    api_key = kw.get("api_key", "") or os.getenv("COINDESK_API_KEY", "")
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://min-api.cryptocompare.com/data/v2/news/",
                params={"lang": "EN", "limit": limit, "api_key": api_key},
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("Response") == "Success" and data.get("Data"):
                    articles = []
                    for item in data["Data"]:
                        articles.append(
                            {
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "source": item.get("source", "CoinDesk"),
                                "published_at": datetime.fromtimestamp(
                                    item.get("published_on", 0),
                                    tz=UTC,
                                ).isoformat(),
                                "description": item.get("body", ""),
                                "category": item.get("categories", "news").lower(),
                                "upvotes": item.get("upvotes", 0),
                                "downvotes": item.get("downvotes", 0),
                            }
                        )
                    return {"articles": articles, "source": "coindesk", "count": len(articles)}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _santiment_dev_activity(project_slug: str = "bitcoin", **kw) -> dict[str, Any] | None:
    """Santiment - GitHub dev activity and social volume (free tier: 100 calls/day)."""
    api_key = kw.get("api_key", "") or os.getenv("SANTIMENT_API_KEY", "")
    if not api_key or not project_slug:
        return None
    try:
        query = f"""
        {{
          getMetric(metric: "dev_activity")
          {{
            timeseriesData(
              slug: "{project_slug}"
              from: "30d_ago"
              to: "now"
              interval: "1d"
            )
          }}
        }}
        """
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://api.santiment.net/graphql",
                json={"query": query},
                headers={"Authorization": f"Apikey {api_key}"},
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "project": project_slug,
                    "dev_activity": data.get("data", {}).get("getMetric", {}).get("timeseriesData", []),
                    "source": "santiment",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _virustotal_url_scan(url: str, **kw) -> dict[str, Any] | None:
    """VirusTotal - scan token website URLs for phishing/malware (free: 500 req/day)."""
    api_key = kw.get("api_key", "") or os.getenv("VIRUSTOTAL_API_KEY", "")
    if not api_key or not url:
        return None
    try:
        import base64

        url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://www.virustotal.com/api/v3/urls/{url_id}", headers={"x-apikey": api_key})
            if r.status_code == 200:
                data = r.json()
                stats = data.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
                return {
                    "url": url,
                    "malicious": stats.get("malicious", 0),
                    "suspicious": stats.get("suspicious", 0),
                    "harmless": stats.get("harmless", 0),
                    "source": "virustotal",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _dune_early_buyers(token_address: str = "", chain: str = "ethereum", **kw) -> dict[str, Any] | None:
    """Dune Analytics - finds the first 5-minute buyers of a token.
    Uses dual-key fallback to double free tier capacity (10k CU/mo per key).
    """
    primary_key = kw.get("api_key", "") or os.getenv("DUNE_API_KEY", "")
    secondary_key = os.getenv("DUNE_API_KEY_2", "")
    if not token_address:
        return None
    query_id = 3946245

    async def _try_dune_key(api_key: str) -> dict[str, Any] | None:
        if not api_key:
            return None
        try:
            headers = {"X-Dune-API-Key": api_key}
            params = {"token_address": token_address.lower(), "chain": chain.lower()}
            async with httpx.AsyncClient(timeout=30) as c:
                exec_url = f"https://api.dune.com/api/v1/query/{query_id}/execute"
                r_exec = await c.post(exec_url, json={"query_parameters": params}, headers=headers)
                if r_exec.status_code in (429, 402, 403):
                    return {"error": "quota_exceeded", "status": r_exec.status_code}
                if r_exec.status_code == 200:
                    exec_id = r_exec.json().get("execution_id")
                    if exec_id:
                        for _ in range(3):
                            await asyncio.sleep(2)
                            r_result = await c.get(
                                f"https://api.dune.com/api/v1/execution/{exec_id}/results",
                                headers=headers,
                            )
                            if r_result.status_code == 200:
                                data = r_result.json()
                                if data.get("state") == "QUERY_STATE_COMPLETED":
                                    return {
                                        "token": token_address,
                                        "chain": chain,
                                        "early_buyers": data.get("result", {}).get("rows", [])[:20],
                                        "source": "dune",
                                    }
                            elif r_result.status_code != 202:
                                break
        except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError) as e:
            logger.warning(f"provider error: {e}")
        return None

    result = await _try_dune_key(primary_key)
    if isinstance(result, dict) and result.get("error") == "quota_exceeded":
        logger.info("Dune primary key quota exceeded, failing over to secondary key")
        result = await _try_dune_key(secondary_key)
    if isinstance(result, dict) and result.get("error") == "quota_exceeded":
        return None
    return result
