"""
Local/internal providers — our own data and passthrough to backend API.

These are always preferred (Tier.LOCAL) because they're instant and free.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import redis

from app.core.logging import get_logger

logger = get_logger("databus.providers.local")


async def _local_token_price(**kwargs) -> dict[str, Any] | None:
    """Get price from our own data (Redis/ClickHouse)."""
    try:
        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        token = kwargs.get("token", "")
        if token:
            cached = r.get(f"price:{token.lower()}")
            if cached:
                return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _local_wallet_labels(address: str = "", **kw) -> dict[str, Any] | None:
    """Get wallet labels from our Wallet Memory Bank (HTTP passthrough)."""
    if not address:
        return None
    try:
        from app.config import settings

        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"http://localhost:8000/api/v1/wallet/labels/{address}",
                headers={"X-API-Key": settings.rmi_auth_token},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _clickhouse_wallet_labels(address: str = "", **kw) -> dict[str, Any] | None:
    """Get wallet labels from ClickHouse directly — ~154k curated labels, LOCAL tier.

    This is the FIRST provider in the wallet_labels chain.
    If ClickHouse has labels, we return them immediately.
    No external API calls, no credit usage, sub-10ms latency.
    Results are cached in Redis on read (write-through).
    """
    if not address:
        return None
    try:
        from app.domains.labels.sources.clickhouse_labels import (
            query_clickhouse_labels,
        )

        chain = kw.get("chain", kw.get("network", ""))
        labels = await query_clickhouse_labels(address, chain)

        if labels:
            # Convert Label objects to dicts
            label_dicts = []
            for lbl in labels:
                d = lbl.to_dict()
                d["source"] = "clickhouse_wallet_memory"
                label_dicts.append(d)

            result = {
                "labels": label_dicts,
                "address": address,
                "chain": chain or "any",
                "source": "clickhouse_wallet_memory",
                "total_labels": "~154k curated",
                "tier": "local",
                "is_local": True,
            }

            # ── Write-through to Redis cache ──
            try:
                import os

                import redis as sync_redis

                r = sync_redis.Redis(
                    host=os.getenv("REDIS_HOST", "rmi-redis"),
                    port=int(os.getenv("REDIS_PORT", "6379")),
                    password=os.getenv("REDIS_PASSWORD", ""),
                    decode_responses=True,
                    socket_connect_timeout=2,
                )
                for entry in label_dicts:
                    lchain = chain
                    if not lchain:
                        lchain = entry.get("chain", "ethereum")
                    key = f"rmi:label:{lchain}:{address}"
                    r.setex(key, 3600, json.dumps(entry))
                r.close()
            except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
                logger.debug("write-through to Redis for ClickHouse labels failed")

            return result
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError) as e:
        logger.debug(f"ClickHouse wallet labels error: {e}")

    return None


async def _passthrough_market_overview(**kwargs) -> dict[str, Any] | None:
    """Market overview from our own backend (DataBus market/brief)."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://localhost:8000/api/v1/databus/market/brief")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _passthrough_trending(**kwargs) -> dict[str, Any] | None:
    """Trending tokens from our own backend (DataBus market/trending)."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://localhost:8000/api/v1/databus/market/trending")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _passthrough_news(**kwargs) -> dict[str, Any] | None:
    """News from our own Redis-cached aggregator — top 50 sources, instant.

    Reads directly from the Redis-backed news_provider.get_full_news_feed()
    (always instant, refreshed every 5 min). Falls back to the HTTP endpoint
    only if the provider module isn't available.
    """
    limit = kwargs.get("limit", 20)

    # Primary: direct call to Redis-backed provider (instant)
    try:
        from app.databus.news_provider import get_full_news_feed

        data = await get_full_news_feed(limit=limit)
        if data and data.get("headlines"):
            return data
    except (ImportError, Exception) as e:
        logger.debug(f"Direct news provider failed: {e}")

    # Fallback: HTTP passthrough to /api/v1/news/headlines (fast, reads cache)
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"http://localhost:8000/api/v1/news/headlines?count={limit}")
            if r.status_code == 200:
                data = r.json()
                hls = data.get("headlines", [])
                if hls:
                    return {
                        "headlines": {
                            "articles": [
                                {
                                    "id": h.get("title", ""),
                                    "title": h.get("title", ""),
                                    "url": h.get("url", ""),
                                    "source": h.get("source", ""),
                                    "published_at": str(h.get("published_at", "")),
                                    "category": h.get("category", ""),
                                    "sentiment": h.get("sentiment", "neutral"),
                                }
                                for h in hls
                            ],
                            "total": len(hls),
                            "source": "rmi_news_aggregator",
                            "free": True,
                        },
                        "source": "full_news_feed",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("news passthrough failed")

    return None


async def _passthrough_alerts(**kwargs) -> dict[str, Any] | None:
    """Active alerts from our own backend."""
    try:
        from app.config import settings

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "http://localhost:8000/api/v1/alerts",
                headers={"X-API-Key": settings.rmi_auth_token},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _passthrough_scanner(address: str = "", chain: str = "solana", **kw) -> dict[str, Any] | None:
    """Token security scan via our SENTINEL scanner (v2 endpoint)."""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "http://localhost:8000/api/v2/scanner/token/scan",
                json={"address": address, "chain": chain},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _passthrough_rag(query: str = "", collection: str = "known_scams", **kw) -> dict[str, Any] | None:
    """RAG search against the rugbrain-api vector store."""
    try:
        from app.config import settings

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "http://rugbrain-api:8012/api/v1/search",
                params={"q": query, "collection": collection, "limit": kw.get("limit", 5)},
                headers={"X-API-Key": settings.rmi_rag_api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


# ── Inline passthrough functions (originally in build_provider_chains) ──


async def _passthrough_hyperliquid(**kwargs: Any) -> dict[str, Any] | None:
    """Hyperliquid perp markets from our own backend."""
    try:
        limit = kwargs.get("limit", 10)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"http://localhost:8000/api/v1/market/hyperliquid?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _passthrough_hyperliquid_action(**kwargs: Any) -> dict[str, Any] | None:
    """Hyperliquid action (gain/loss, squeezes) from our own backend."""
    try:
        limit = kwargs.get("limit", 5)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"http://localhost:8000/api/v1/market/hyperliquid-action?limit={limit}",
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _passthrough_insider_wallets(**kwargs: Any) -> dict[str, Any] | None:
    """Insider wallets from our own backend."""
    try:
        limit = kwargs.get("limit", 10)
        tier = kwargs.get("tier", "free")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"http://localhost:8000/api/v1/market/insider-wallets?limit={limit}&tier={tier}",
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _passthrough_prediction_signals(**kwargs: Any) -> dict[str, Any] | None:
    """Prediction market signals from our own backend."""
    try:
        limit = kwargs.get("limit", 5)
        tier = kwargs.get("tier", "free")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"http://localhost:8000/api/v1/market/prediction-signals?limit={limit}&tier={tier}",
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None
