"""Passthrough/local providers — call our own backend endpoints."""

from __future__ import annotations
import httpx

from sdks.python.x402_frameworks.autogen_adapter import logger

async def _passthrough_market_overview(**kwargs) -> dict | None:
    """Market overview from our own /api/v1/content/market-overview."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://localhost:8000/api/v1/content/market-overview")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None

async def _passthrough_trending(**kwargs) -> dict | None:
    """Trending tokens from our own /api/v1/tokens/trending."""
    try:
        limit = kwargs.get("limit", 20)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"http://localhost:8000/api/v1/tokens/trending?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None

async def _passthrough_news(**kwargs) -> dict | None:
    """News from our own 30-source aggregator."""
    try:
        limit = kwargs.get("limit", 20)
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"http://localhost:8000/api/v1/news/combined?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None

async def _passthrough_alerts(**kwargs) -> dict | None:
    """Alerts from our real alert pipeline."""
    try:
        from app.alert_pipeline import get_active_alert_count, get_recent_alerts

        count = await get_active_alert_count()
        recent = await get_recent_alerts(limit=kwargs.get("limit", 20))
        return {"count": count, "alerts": recent}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return {"count": 0, "alerts": []}

async def _local_wallet_labels(address: str = "", **kw) -> dict | None:
    """Our 190K wallet labels from Redis - rmi:label:{chain}:{address} format."""
    if not address:
        return None
    try:
        import json
        import os

        import redis
        from dotenv import load_dotenv

        load_dotenv("/app/.env", override=True)
        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        chains = [
            "solana", "ethereum", "bsc", "base", "arbitrum",
            "optimism", "polygon", "avalanche",
        ]
        for chain in chains:
            key = f"rmi:label:{chain}:{address}"
            data = r.get(key)
            if data:
                label = json.loads(data)
                r.close()
                return {
                    "labels": [label],
                    "address": address,
                    "source": "wallet_memory_bank",
                    "total_labels": "190K+",
                }
        keys = r.keys(f"rmi:label:*:{address}")
        if keys:
            data = r.get(keys[0])
            if data:
                label = json.loads(data)
                r.close()
                return {
                    "labels": [label],
                    "address": address,
                    "source": "wallet_memory_bank",
                    "total_labels": "190K+",
                }
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None

async def _passthrough_scanner(address: str = "", chain: str = "solana", **kw) -> dict | None:
    """SENTINEL scanner - our own token security analysis."""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "http://localhost:8000/api/v1/token/scan",
                json={"address": address, "chain": chain},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None

async def _passthrough_rag(query: str = "", collection: str = "known_scams", **kw) -> dict | None:
    """RAG search - our 17K+ document knowledge base."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "http://localhost:8000/api/v1/rag/search",
                json={"query": query, "collection": collection, "limit": kw.get("limit", 10)},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
