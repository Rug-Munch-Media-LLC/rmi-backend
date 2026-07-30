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


async def _local_token_price(**kwargs) -> dict | None:
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _local_wallet_labels(address: str = "", **kw) -> dict | None:
    """Get wallet labels from our Wallet Memory Bank (HTTP passthrough)."""
    if not address:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"http://localhost:8000/api/v1/wallet/labels/{address}",
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _clickhouse_wallet_labels(address: str = "", **kw) -> dict | None:
    """Get wallet labels from ClickHouse directly — 39.4M labels, LOCAL tier.

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
                "total_labels": "39.4M+",
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
                for lbl in label_dicts:
                    lchain = chain
                    if not lchain:
                        lchain = lbl.get("chain", "ethereum")
                    key = f"rmi:label:{lchain}:{address}"
                    r.setex(key, 3600, json.dumps(lbl))
                r.close()
            except (ValueError, ConnectionError, TimeoutError, RuntimeError):
                logger.debug("write-through to Redis for ClickHouse labels failed")

            return result
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError) as e:
        logger.debug(f"ClickHouse wallet labels error: {e}")

    return None


async def _passthrough_market_overview(**kwargs) -> dict | None:
    """Market overview from our own backend."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://localhost:8000/api/v1/market/overview")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_trending(**kwargs) -> dict | None:
    """Trending tokens from our own backend."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://localhost:8000/api/v1/market/trending")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_news(**kwargs) -> dict | None:
    """News from our own backend — 200+ sources, quality-scored."""
    try:
        limit = kwargs.get("limit", 20)
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"http://localhost:8000/api/v1/news/feed?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")

    # Fallback: try the domain POST endpoint
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "http://localhost:8000/api/v1/news",
                json={"limit": kwargs.get("limit", 20)},
            )
            if r.status_code == 200:
                data = r.json()
                items = data.get("items", [])
                return {
                    "articles": [
                        {
                            "id": a.get("news_id", ""),
                            "title": a.get("title", ""),
                            "url": a.get("url", ""),
                            "source": a.get("source", "RMI News"),
                            "published_at": str(a.get("published_at", "")),
                            "description": a.get("summary", ""),
                            "category": "news",
                            "sentiment": "neutral",
                        }
                        for a in items
                    ],
                    "total": len(items),
                    "source": "rmi_news_fallback",
                    "free": True,
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("news fallback also failed")
    return None


async def _passthrough_alerts(**kwargs) -> dict | None:
    """Active alerts from our own backend."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://localhost:8000/api/v1/alerts")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_scanner(address: str = "", chain: str = "solana", **kw) -> dict | None:
    """Token security scan via our SENTINEL scanner."""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"http://localhost:8000/api/v1/scan/{chain}/{address}",
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_rag(query: str = "", collection: str = "known_scams", **kw) -> dict | None:
    """RAG search against our vector store."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"http://localhost:8000/api/v1/rag/search?q={query}&collection={collection}",
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── Inline passthrough functions (originally in build_provider_chains) ──


async def _passthrough_hyperliquid(**kwargs: Any) -> dict | None:
    """Hyperliquid perp markets from our own backend."""
    try:
        limit = kwargs.get("limit", 10)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"http://localhost:8000/api/v1/market/hyperliquid?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_hyperliquid_action(**kwargs: Any) -> dict | None:
    """Hyperliquid action (gain/loss, squeezes) from our own backend."""
    try:
        limit = kwargs.get("limit", 5)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"http://localhost:8000/api/v1/market/hyperliquid-action?limit={limit}",
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_insider_wallets(**kwargs: Any) -> dict | None:
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_prediction_signals(**kwargs: Any) -> dict | None:
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
