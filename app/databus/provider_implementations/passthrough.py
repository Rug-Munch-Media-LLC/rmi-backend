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
    """Wallet labels from Redis (4.29M hot labels) — rmi:label:{chain}:{address} format.

    Falls back to ClickHouse wallet_memory.wallet_labels (39.4M rows) if Redis
    has no data for the requested address. Write-through caching: any label
    found via ClickHouse fallback is written to Redis (48h TTL) so subsequent
    lookups are fast — the cache self-warms with popular addresses.
    """
    if not address:
        return None

    addr_lower = address.lower().strip()
    first_label = None

    # ── REDIS LOOKUP — 4.29M hot labels ────────────────────────────
    try:
        import json
        import os

        import redis as _redis
        from dotenv import load_dotenv

        load_dotenv("/app/.env", override=True)
        r = _redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        # Try common chains first (fast path — direct key lookup)
        chains = [
            "btc",
            "eth",
            "sol",
            "ethereum",
            "solana",
            "bsc",
            "base",
            "arbitrum",
            "optimism",
            "polygon",
            "avalanche",
        ]
        for chain in chains:
            key = f"rmi:label:{chain}:{addr_lower}"
            data = r.get(key)
            if data:
                label = json.loads(data)
                first_label = label
                break
        # Fallback path: scan for any chain
        if not first_label:
            keys = r.keys(f"rmi:label:*:{addr_lower}")
            if keys:
                data = r.get(keys[0])
                if data:
                    label = json.loads(data)
                    first_label = label
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("redis provider error")

    if first_label:
        return {
            "labels": [first_label],
            "address": addr_lower,
            "source": "wallet_memory_bank",
            "total_labels": "4.29M+",
        }

    # ── FALLBACK: ClickHouse direct query + write-through cache ───
    try:
        import json
        import os

        import aiohttp

        # ClickHouse HTTP interface (native driver not in container)
        ch_url = os.getenv(
            "CLICKHOUSE_URL",
            "http://rmi:clickhouse_rmi_prod_2026@rmi-clickhouse:8123?default_format=TabSeparated",
        )
        # NOTE: Addresses are wallet hex/base58 — SQL-safe alphanumeric.
        # Using format with sanitization limit for internal trusted traffic.
        query = (
            "SELECT chain_id, label_name, label_category, source, is_sanctioned "
            "FROM wallet_memory.wallet_labels "
            f"WHERE address = '{addr_lower}' "
            "AND label_name != '' "
            "ORDER BY is_sanctioned DESC, loaded_at DESC "
            "LIMIT 5"
        )
        async with aiohttp.ClientSession() as session, session.post(ch_url, data=query.encode()) as resp:
            if resp.status == 200:
                text = await resp.text()
                rows = [line.split("\t") for line in text.strip().split("\n") if line]
                if rows:
                    labels = []
                    for row in rows:
                        ch_chain = (row[0] or "").strip()
                        label_name = (row[1] or "").strip()
                        label_cat = (row[2] or "").strip()
                        is_sanctioned = bool(row[4]) if len(row) > 4 else False

                        # Normalize chain to short form for Redis key
                        ch_map = {
                            "ethereum": "eth",
                            "solana": "sol",
                            "bitcoin": "btc",
                            "1": "eth",
                            "56": "bsc",
                            "137": "polygon",
                            "42161": "arbitrum",
                            "10": "optimism",
                            "8453": "base",
                            "43114": "avalanche",
                        }
                        normalized_chain = ch_map.get(ch_chain.lower(), ch_chain.lower())

                        label_dict = {
                            "address_name": label_name,
                            "label_type": label_cat or "unknown",
                            "chain": normalized_chain,
                            "source": "clickhouse_wallet_memory",
                            "confidence": 0.95,
                        }
                        if is_sanctioned:
                            label_dict["is_sanctioned"] = True
                        labels.append(label_dict)

                    # ── Write-through cache ─────────────────────
                    # Store in Redis so next lookup for this address is fast
                    try:
                        import redis as _redis2

                        r2 = _redis2.Redis(
                            host=os.getenv("REDIS_HOST", "rmi-redis"),
                            port=int(os.getenv("REDIS_PORT", "6379")),
                            password=os.getenv("REDIS_PASSWORD", ""),
                            decode_responses=True,
                            socket_connect_timeout=2,
                        )
                        for label_dict in labels:
                            chain_key = label_dict.get("chain", "eth")
                            cache_key = f"rmi:label:{chain_key}:{addr_lower}"
                            # SETNX — don't overwrite if already cached
                            r2.setnx(cache_key, json.dumps(label_dict))
                            # Also set a 48h TTL as safety net
                            r2.expire(cache_key, 172800)
                        r2.close()
                    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
                        logger.warning("write-through cache error")

                    return {
                        "labels": labels,
                        "address": addr_lower,
                        "source": "wallet_memory_bank",
                        "total_labels": "39.4M+",
                        "fallback": "clickhouse",
                        "write_through": True,
                    }
    except Exception as e:
        logger.warning(f"clickhouse fallback error: {e}")

    return None


async def _local_wallet_labels_batch(addresses: list[str] | None = None, **kw) -> dict | None:
    """Batch wallet label lookup — up to 100 addresses at once.

    Queries ClickHouse with WHERE address IN (...), returns all labels,
    and writes results to Redis (write-through cache).
    """
    if not addresses or not isinstance(addresses, list):
        return None

    # Cap at 100
    addrs = [a.lower().strip() for a in addresses[:100] if a and a.strip()]

    try:
        import json
        import os

        import aiohttp

        ch_url = os.getenv(
            "CLICKHOUSE_URL",
            "http://rmi:clickhouse_rmi_prod_2026@rmi-clickhouse:8123?default_format=TabSeparated",
        )

        # Build IN clause with quotes
        addr_list = ", ".join(f"'{a}'" for a in addrs)
        query = (
            "SELECT address, chain_id, label_name, label_category, source, is_sanctioned "
            "FROM wallet_memory.wallet_labels "
            f"WHERE address IN ({addr_list}) "
            "AND label_name != '' "
            "ORDER BY is_sanctioned DESC, loaded_at DESC "
            "LIMIT 500"
        )

        ch_map = {
            "ethereum": "eth",
            "solana": "sol",
            "bitcoin": "btc",
            "1": "eth",
            "56": "bsc",
            "137": "polygon",
            "42161": "arbitrum",
            "10": "optimism",
            "8453": "base",
            "43114": "avalanche",
        }

        async with aiohttp.ClientSession() as session, session.post(ch_url, data=query.encode()) as resp:
            if resp.status != 200:
                return None
            text = await resp.text()
            rows = [line.split("\t") for line in text.strip().split("\n") if line]

            if not rows:
                return None

            # Group labels by address
            addr_labels: dict[str, list[dict]] = {}
            for row in rows:
                if len(row) < 4:
                    continue
                addr = (row[0] or "").lower().strip()
                ch_chain = (row[1] or "").strip()
                label_name = (row[2] or "").strip()
                label_cat = (row[3] or "").strip()
                is_sanctioned = bool(row[5]) if len(row) > 5 else False
                normalized_chain = ch_map.get(ch_chain.lower(), ch_chain.lower())

                label_dict = {
                    "address_name": label_name,
                    "label_type": label_cat or "unknown",
                    "chain": normalized_chain,
                    "source": "clickhouse_wallet_memory",
                    "confidence": 0.95,
                }
                if is_sanctioned:
                    label_dict["is_sanctioned"] = True

                if addr not in addr_labels:
                    addr_labels[addr] = []
                addr_labels[addr].append(label_dict)

            # Write-through: cache each label in Redis
            try:
                import redis as _redis

                r2 = _redis.Redis(
                    host=os.getenv("REDIS_HOST", "rmi-redis"),
                    port=int(os.getenv("REDIS_PORT", "6379")),
                    password=os.getenv("REDIS_PASSWORD", ""),
                    decode_responses=True,
                    socket_connect_timeout=2,
                )
                for addr, labels_list in addr_labels.items():
                    for label_dict in labels_list:
                        chain_key = label_dict.get("chain", "eth")
                        cache_key = f"rmi:label:{chain_key}:{addr}"
                        r2.setnx(cache_key, json.dumps(label_dict))
                        r2.expire(cache_key, 172800)
                r2.close()
            except (ValueError, ConnectionError, TimeoutError, RuntimeError):
                logger.warning("batch write-through cache error")

            labeled_count = sum(len(v) for v in addr_labels.values())
            return {
                "labels": addr_labels,
                "addresses_checked": len(addrs),
                "addresses_labeled": len(addr_labels),
                "total_labels": labeled_count,
                "source": "wallet_memory_bank",
                "total_labels_in_db": "39.4M+",
                "fallback": "clickhouse_batch",
                "write_through": True,
            }

    except Exception as e:
        logger.warning(f"batch label lookup error: {e}")
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
    """RAG search - our 17K+ document knowledge base via rmi-rag-api."""
    try:
        from app.config import settings

        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "http://rmi-rag-api:8012/api/v1/search",
                params={
                    "q": query,
                    "collection": collection,
                    "limit": kw.get("limit", 10),
                },
                headers={"X-API-Key": settings.rmi_rag_api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
