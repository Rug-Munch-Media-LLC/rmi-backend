"""API and internal fetcher functions (CoinGecko, CryptoPanic, RMI internal, Ghost)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domains.news.news_service.service import NewsService

logger = get_logger(__name__)


async def fetch_coin_gecko_news() -> list[dict[str, Any]]:
    """Fetch crypto news from CoinGecko's free API (no key required)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.coingecko.com/api/v3/news")
            if resp.status_code != 200:
                return []
            data = resp.json()
        return [
            {
                "id": f"coingecko-{a.get('id', '')[:12]}",
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "description": a.get("description", ""),
                "source": "CoinGecko",
                "published_at": a.get("created_at", datetime.now(UTC).isoformat()),
                "category": "general",
                "sentiment": "neutral",
                "kind": "external",
                "tier": "free",
            }
            for a in (data.get("data", []))[:10]
        ]
    except (httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError, ValueError, KeyError):
        return []


async def fetch_crypto_panic_news() -> list[dict[str, Any]]:
    """Fetch crypto news from CryptoPanic's free tier."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://cryptopanic.com/api/v1/posts/",
                params={"auth_token": "", "public": "true"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        return [
            {
                "id": f"cryptopanic-{r.get('id', '')}",
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "description": r.get("description", ""),
                "source": "CryptoPanic",
                "published_at": r.get("created_at", datetime.now(UTC).isoformat()),
                "category": r.get("kind", "news"),
                "sentiment": "neutral",
                "kind": "external",
                "tier": "free",
            }
            for r in (data.get("results", []))[:10]
        ]
    except (httpx.HTTPError, httpx.TimeoutException, ConnectionError, OSError, ValueError, KeyError):
        return []


async def fetch_rmi_alerts(svc: NewsService) -> list[dict[str, Any]]:
    """Fetch scam alerts from our own Qdrant knowledge base via rugbrain."""
    try:
        from app.integrations.rugbrain_client import rugbrain_search

        hits = await rugbrain_search(
            query="scam exploit hack rug drain alert",
            collections=["threat_intel_v2"],
            limit=5,
        )
        if not hits:
            return []
        articles = []
        for r in hits[:5]:
            text = (r.get("text") or r.get("content") or "")[:300]
            title = (r.get("title") or text[:80]) or "Alert"
            ch = hashlib.sha256(f"alert:{title}".encode()).hexdigest()
            if ch in svc.seen_hashes:
                continue
            svc.seen_hashes.add(ch)
            articles.append(
                {
                    "id": f"alert-{ch[:12]}",
                    "title": f"🚨 {title}",
                    "url": r.get("url", "https://rugmunch.io/alerts"),
                    "description": text,
                    "source": "RMI Alerts",
                    "published_at": r.get("published_at", datetime.now(UTC).isoformat()),
                    "category": "security",
                    "sentiment": "bearish",
                    "kind": "internal",
                    "tier": "rmi",
                }
            )
        return articles
    except Exception as exc:
        logger.debug("fetch_rmi_alerts_failed", error=str(exc)[:120])
        return []
