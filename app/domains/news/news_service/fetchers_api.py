"""API and internal fetcher functions (CoinGecko, CryptoPanic, RMI internal, Ghost)."""

from __future__ import annotations

import hashlib
import json
import requests
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger
from app.domains.news.news_service.sources import (
    BACKEND_URL,
    COINGECKO_API_KEY,
    CRYPTOPANIC_API_KEY,
    GHOST_CONTENT_KEY,
    GHOST_URL,
)

if TYPE_CHECKING:
    from app.domains.news.news_service.service import NewsService

logger = get_logger(__name__)


async def fetch_coingecko(svc: NewsService, limit: int = 15) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
            resp = await client.get(
                "https://api.coingecko.com/api/v3/news", params={"per_page": limit}, headers=headers
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        articles = []
        for item in data.get("data", []):
            title, url = item.get("title", ""), item.get("url", "")
            desc = item.get("description", "") or title
            pub_str = item.get("updated_at", item.get("created_at", ""))
            ch = hashlib.sha256(f"cg:{title}:{url}".encode()).hexdigest()
            if ch in svc.seen_hashes:
                continue
            svc.seen_hashes.add(ch)
            try:
                published = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).isoformat()
            except (ValueError, TypeError):
                published = datetime.now(UTC).isoformat()
            tfa = f"{title} {desc}"
            articles.append(
                {
                    "id": f"cg-{ch[:12]}",
                    "title": title,
                    "url": url,
                    "description": desc[:300],
                    "source": "CoinGecko",
                    "published_at": published,
                    "category": svc._categorize(tfa),
                    "sentiment": svc._analyze_sentiment(tfa),
                    "kind": "api",
                    "tier": "market",
                }
            )
        return articles
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
        logger.debug("fetch_coingecko_error", exc_info=True)
        logger.debug(f"CoinGecko failed: {e}")
        return []


async def fetch_cryptopanic(svc: NewsService, limit: int = 15) -> list[dict[str, Any]]:
    try:
        params = {"public": "true", "kind": "news", "limit": min(limit, 20)}
        if CRYPTOPANIC_API_KEY:
            params["auth_token"] = CRYPTOPANIC_API_KEY
            url = "https://cryptopanic.com/api/v1/posts/"
        else:
            url = "https://cryptopanic.com/api/free/v1/posts/"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
        articles = []
        for item in data.get("results", []):
            title, url = item.get("title", ""), item.get("url", "")
            src = item.get("source", {})
            source_title = src.get("title", "CryptoPanic") if isinstance(src, dict) else "CryptoPanic"
            pub_str = item.get("published_at", item.get("created_at", ""))
            ch = hashlib.sha256(f"cp:{title}:{url}".encode()).hexdigest()
            if ch in svc.seen_hashes:
                continue
            svc.seen_hashes.add(ch)
            try:
                published = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).isoformat()
            except (ValueError, TypeError):
                published = datetime.now(UTC).isoformat()
            votes = item.get("votes", {})
            bull = votes.get("positive", 0) if isinstance(votes, dict) else 0
            bear = votes.get("negative", 0) if isinstance(votes, dict) else 0
            sentiment = "bullish" if bull > bear * 2 else "bearish" if bear > bull * 2 else "neutral"
            articles.append(
                {
                    "id": f"cp-{ch[:12]}",
                    "title": title,
                    "url": url,
                    "source": source_title,
                    "published_at": published,
                    "category": svc._categorize(title),
                    "sentiment": sentiment,
                    "kind": "api",
                    "tier": "market",
                }
            )
        return articles
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
        logger.debug("fetch_cryptopanic_error", exc_info=True)
        logger.debug(f"CryptoPanic failed: {e}")
        return []


async def fetch_rmi_scanner(svc: NewsService) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{BACKEND_URL}/api/v1/scanner/recent?limit=10")
            if resp.status_code != 200:
                return []
            data = resp.json()
        articles = []
        for scan in data[:10]:
            token = scan.get("token_name", "Unknown Token")
            address = scan.get("contract_address", scan.get("token_address", ""))
            chain = scan.get("chain", "")
            risk = scan.get("risk_score", 0)
            flags = []
            if scan.get("is_honeypot"):
                flags.append("HONEYPOT")
            if scan.get("is_rug_pull"):
                flags.append("RUG PULL RISK")
            if not scan.get("liquidity_locked"):
                flags.append("UNLOCKED LP")
            if scan.get("has_mint_authority"):
                flags.append("MINTABLE")
            flag_str = ", ".join(flags) if flags else "clean"
            ch = hashlib.sha256(f"scanner:{address}:{chain}".encode()).hexdigest()
            if ch in svc.seen_hashes:
                continue
            svc.seen_hashes.add(ch)
            sentiment = "bearish" if risk > 60 else "slightly_bearish" if risk > 30 else "neutral"
            articles.append(
                {
                    "id": f"scan-{ch[:12]}",
                    "title": f"{token} ({chain.upper()}) - Risk: {risk}/100 [{flag_str}]",
                    "url": f"https://rugmunch.io/scanner?address={address}&chain={chain}",
                    "description": f"Scanner flagged {token} on {chain}. Risk: {risk}/100. Flags: {flag_str}.",
                    "source": "RMI Scanner",
                    "published_at": scan.get("scanned_at", datetime.now(UTC).isoformat()),
                    "category": "security",
                    "sentiment": sentiment,
                    "kind": "internal",
                    "tier": "rmi",
                    "risk_score": risk,
                    "token_address": address,
                    "chain": chain,
                }
            )
        return articles
    except (
        httpx.HTTPError,
        requests.RequestException,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        KeyError,
        ConnectionError,
        OSError,
        TimeoutError,
    ) as e:
        logger.debug("fetch_rmi_scanner_error", exc_info=True)
        logger.warning(f"RMI Scanner fetch failed: {e}")
        return []


async def fetch_rmi_alerts(svc: NewsService) -> list[dict[str, Any]]:
    try:
        from app.config import settings

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "http://rmi-rag-api:8012/api/v1/search",
                params={
                    "q": "scam exploit hack rug drain alert",
                    "collection": "scam_intel",
                    "limit": 5,
                },
                headers={"X-API-Key": settings.rmi_rag_api_key},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
        articles = []
        for r in (data.get("results", data.get("data", [])))[:5]:
            title = (r.get("title", r.get("content", "")) or "")[:120]
            content = (r.get("content", r.get("description", "")) or "")[:300]
            ch = hashlib.sha256(f"alert:{title}".encode()).hexdigest()
            if ch in svc.seen_hashes:
                continue
            svc.seen_hashes.add(ch)
            articles.append(
                {
                    "id": f"alert-{ch[:12]}",
                    "title": f"🚨 {title}",
                    "url": r.get("url", "https://rugmunch.io/alerts"),
                    "description": content,
                    "source": "RMI Alerts",
                    "published_at": r.get("created_at", r.get("published_at", datetime.now(UTC).isoformat())),
                    "category": "security",
                    "sentiment": "bearish",
                    "kind": "internal",
                    "tier": "rmi",
                }
            )
        return articles
    except (
        httpx.HTTPError,
        requests.RequestException,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        KeyError,
        ConnectionError,
        OSError,
        TimeoutError,
    ) as e:
        logger.debug("fetch_rmi_alerts_error", exc_info=True)
        logger.debug(f"RMI Alerts failed: {e}")
        return []


async def fetch_ghost(svc: NewsService, limit: int = 5) -> list[dict[str, Any]]:
    if not GHOST_CONTENT_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{GHOST_URL}/ghost/api/content/posts/",
                params={
                    "key": GHOST_CONTENT_KEY,
                    "limit": limit,
                    "fields": "title,url,published_at,excerpt,slug",
                    "order": "published_at DESC",
                },
            )
            data = resp.json()
            return [
                {
                    "id": f"ghost-{p.get('id', '')}",
                    "title": p.get("title"),
                    "url": f"https://blog.rugmunch.io/{p.get('slug', '')}/",
                    "description": (p.get("excerpt", "") or "")[:200],
                    "source": "RMI Blog",
                    "published_at": p.get("published_at"),
                    "category": "analysis",
                    "sentiment": "neutral",
                    "kind": "internal",
                    "tier": "rmi",
                }
                for p in data.get("posts", [])
            ]
    except (
        httpx.HTTPError,
        requests.RequestException,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ConnectionError,
        OSError,
        TimeoutError,
    ) as e:
        logger.debug("fetch_ghost_error", exc_info=True)
        logger.debug(f"Ghost fetch failed: {e}")
        return []
