"""
DataBus Client — THE Single Source of Truth for All Data Requests.

Every data request in the entire platform routes through this class.
Cache(SWR) → Dedup → Provider Chain → Broadcast. All in one place.

Improvements over v1:
  - Stale-While-Revalidate: Users never wait for cache refresh
  - Request Deduplication: Same query within 5s shares one API call
  - Cache Warm: Background prefetch for hot data types
  - Provider Health: Per-provider success/failure/latency tracking
  - Per-type cache stats with TTL tuning suggestions
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.databus.access_control import access_controller
from app.databus.cache import CacheLayer, get_cache
from app.databus.providers import ProviderChain, ProviderTier, build_provider_chains
from app.databus.security import security
from app.databus.services.dedup import RequestDeduplicator
from app.databus.services.health import ProviderHealthTracker
from app.databus.vault import DataBusVault, get_vault

logger = get_logger("databus.client")

# Hard wall-clock budget for one full provider-chain walk. Chains that
# burn through every provider (dead upstreams, 30s client timeouts) were
# observed taking 20s+ per fetch; this caps the damage. Tune with
# DATABUS_FETCH_TIMEOUT (seconds).
FETCH_TIMEOUT_S = float(os.getenv("DATABUS_FETCH_TIMEOUT", "12"))


class DataBus:
    """
    The One True Data Layer. Routes every data request through:
      1. Security check (admin gating, key isolation)
      2. Dedup check (in-flight request deduplication)
      3. Cache(SWR) (stale-while-revalidate: return stale, refresh in bg)
      4. Provider chain (our data first, then free APIs, then paid)
      5. Provider health tracking (circuit breakers, latency)
      6. RAG indexing (optional, for important results)
      7. WebSocket broadcast (for real-time updates)
      8. Cache warm (background prefetch for hot data)
    """

    def __init__(self) -> None:
        self.cache: CacheLayer = get_cache()
        self.vault: DataBusVault | None = None  # loaded async
        self.chains: dict[str, ProviderChain] = {}
        self.security = security
        self._initialized = False
        self._init_lock = asyncio.Lock()
        # Request deduplication
        self._dedup = RequestDeduplicator()
        # Provider health
        self._provider_health = ProviderHealthTracker()
        # Cache warm task
        self._warm_task: asyncio.Task[Any] | None = None
        self._warm_running = False
        # Stats
        self._stats = {
            "total_fetches": 0,
            "cache_hits": 0,
            "cache_stale_hits": 0,
            "cache_misses": 0,
            "dedup_hits": 0,
            "fallback_uses": 0,
            "local_hits": 0,
            "free_api_hits": 0,
            "paid_api_hits": 0,
            "circuit_breaker_rejects": 0,
            "errors": 0,
            "start_time": time.time(),
        }
        # Track expensive calls (Arkham credits)
        self._credit_usage: dict[str, int] = {}
        # SWR refresh callback: wired to self._swr_refresh
        self.cache.stale_refresh_callback = self._swr_refresh

    async def initialize(self) -> None:
        """Async init - load vault keys and build provider chains."""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            # Load vault (GPG-encrypted keys)
            self.vault = await get_vault()
            # Eagerly connect L2 Redis cache (don't wait for first fetch)
            await self.cache.l2._connect()
            # Build fallback chains
            self.chains = build_provider_chains()
            # Register Bitquery provider for blockchain data
            try:
                from app.databus.bitquery_provider import BitqueryProvider

                self.bitquery = BitqueryProvider(self.cache)
                logger.info("Bitquery provider registered")
            except Exception as e:
                logger.warning(f"Bitquery provider not available: {e}")
            self._initialized = True
            total_providers = sum(len(c.providers) for c in self.chains.values())
            logger.info(
                f"DataBus initialized: {len(self.chains)} chains, "
                f"{total_providers} total providers, "
                f"{len(self.vault.pools)} keyed providers"
            )

    # ── Stale-While-Revalidate Background Refresh ──────────────────

    async def _swr_refresh(self, cache_key: str) -> None:
        """Background refresh callback for SWR cache entries.

        Called when a cache.get() returns stale data.
        Re-fetches from provider chain, updates cache, broadcasts.
        """
        parts = cache_key.split(":")
        if len(parts) < 2:
            return
        data_type = parts[1]
        chain = self.chains.get(data_type)
        if not chain:
            return
        try:
            result = await chain.fetch(vault=self.vault, cache=self.cache)
            if result:
                await self.cache.set(cache_key, result, data_type=data_type)
                if data_type in ("token_price", "alerts", "entity_intel"):
                    try:
                        from app.caching_shield.ws_broadcaster import get_ws_manager

                        ws = get_ws_manager()
                        await ws.broadcast(data_type, result)
                    except (AttributeError, ImportError) as e:
                        logger.debug(f"_swr_refresh failed: {e}")
        except (ConnectionError, OSError, TimeoutError) as e:
            logger.debug(f"SWR refresh failed for {data_type}: {e}")

    # ── Cache Warm Prefetch ────────────────────────────────────────

    async def start_cache_warm(self, interval_seconds: int = 60) -> None:
        """Start background cache warm task.

        Pre-fetches hot data types so users always get cache hits.
        OPTIMIZED FOR FREE TIER: Only warms LOCAL and FREE_API endpoints
        to avoid burning Arkham/Alchemy premium credits.
        """
        if self._warm_running:
            return
        self._warm_running = True
        self._warm_types: list[dict[str, Any]] = [
            {"data_type": "market_overview", "kwargs": {}},
            {"data_type": "trending", "kwargs": {}},
            {"data_type": "market_brief", "kwargs": {}},
            {"data_type": "alerts", "kwargs": {}},
            {"data_type": "news", "kwargs": {}},
            {"data_type": "token_price", "kwargs": {}},
            {"data_type": "token_metadata", "kwargs": {}},
            {"data_type": "holder_data", "kwargs": {}},
            {"data_type": "wallet_labels", "kwargs": {}},
            {"data_type": "wallet_tokens", "kwargs": {}},
            {"data_type": "live_prices", "kwargs": {}},
        ]
        self._warm_task = asyncio.create_task(self._warm_loop(interval_seconds))
        logger.info(f"Cache warm started (interval={interval_seconds}s, {len(self._warm_types)} FREE types)")

    async def stop_cache_warm(self) -> None:
        """Stop the cache warm task."""
        self._warm_running = False
        if self._warm_task:
            self._warm_task.cancel()
            self._warm_task = None

    async def _warm_loop(self, interval: int) -> None:
        """Background loop that pre-fetches hot data types."""
        while self._warm_running:
            try:
                for item in self._warm_types:
                    with contextlib.suppress(
                        ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException
                    ):
                        await self.fetch(
                            data_type=item["data_type"],
                            force_fresh=True,
                            consumer_type="authenticated",
                            **item.get("kwargs", {}),
                        )
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                httpx.HTTPError,
                httpx.TimeoutException,
            ) as e:
                logger.warning(f"Cache warm loop error: {e}")
                await asyncio.sleep(interval)

    # ── THE Fetch Method ───────────────────────────────────────────

    async def fetch(
        self,
        data_type: str,
        admin_key: str | None = "",
        force_fresh: bool = False,
        rag_index: bool = False,
        consumer_type: str = "",
        tool_id: str = "",
        x402_tier: str = "",
        request=None,
        service_key: str | None = "",
        **kwargs,
    ) -> dict[str, Any] | None:
        """
        THE method. Fetches data through the full pipeline.

        Pipeline:
          1. Security check → 2. Dedup check → 3. Cache(SWR) →
          4. Provider chain → 5. Health tracking → 6. Cache result →
          7. RAG → 8. WS broadcast → 9. Access control packaging

        Returns:
            dict with: data, source, tier, latency_ms, fallback_used, is_local, cached
        """
        if not self._initialized:
            await self.initialize()

        self._stats["total_fetches"] += 1

        # ── 1. SECURITY CHECK ──
        access_level = self.security.get_access_level(data_type)
        if not self.security.check_access(data_type, admin_key=admin_key, service_key=service_key):
            consumer = access_controller.identify_consumer(
                admin_key=admin_key,
                consumer_type=consumer_type,
                tool_id=tool_id,
                x402_tier=x402_tier,
                service_key=service_key,
            )
            packaging = access_controller.check_access(data_type, consumer)
            if packaging == "denied":
                return {
                    "error": "access_denied",
                    "level": access_level,
                    "message": f"Access to {data_type} requires {access_level} access",
                }

        # ── 2. DEFAULT CONSUMER ──
        if not consumer_type and not tool_id and not x402_tier:
            consumer_type = "authenticated"

        # ── 3. CREDIT MANAGEMENT ──
        chain = self.chains.get(data_type)
        if not chain:
            self._stats["errors"] += 1
            return {
                "error": "unknown_data_type",
                "message": f"No provider chain for '{data_type}'",
            }

        # ── 4. CACHE CHECK (with SWR) ──
        cache_key = self.cache.make_key(data_type, data_type, **kwargs)
        if not force_fresh:
            cached, is_stale = await self.cache.get(cache_key, data_type=data_type)
            if cached is not None:
                if is_stale:
                    self._stats["cache_stale_hits"] += 1
                    cached["cached"] = "stale"
                else:
                    self._stats["cache_hits"] += 1
                    cached["cached"] = True
                return cached

        self._stats["cache_misses"] += 1

        # ── 5. REQUEST DEDUPLICATION ──
        dedup_key = self._dedup.make_key(data_type, **kwargs)
        future, is_duplicate = await self._dedup.get_or_create(dedup_key)
        if is_duplicate:
            self._stats["dedup_hits"] += 1
            try:
                result = await asyncio.wait_for(future, timeout=30)
                return result
            except (ConnectionError, OSError, TimeoutError) as e:
                logger.warning(f"fetch failed: {e}")

        # ── 5.5 LOCAL PRECHECK ──
        local_result = await self._local_precheck(data_type, **kwargs)
        if local_result:
            self._stats["local_hits"] += 1
            await self.cache.set(cache_key, local_result, data_type=data_type)
            self._dedup.resolve(dedup_key, local_result)
            return local_result

        # ── 6. PROVIDER CHAIN ──
        result = None
        try:
            result = await asyncio.wait_for(
                chain.fetch(vault=self.vault, cache=self.cache, **kwargs),
                timeout=FETCH_TIMEOUT_S,
            )
            if result:
                source = result.get("source", "unknown")
                latency = result.get("latency_ms", 0)
                self._provider_health.record_success(source, latency)
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            httpx.HTTPError,
            httpx.TimeoutException,
        ) as e:
            self._provider_health.record_failure(data_type, str(e))
            self._dedup.fail(dedup_key, e)
            self._stats["errors"] += 1
            return None

        if result is None:
            self._stats["errors"] += 1
            self._dedup.resolve(dedup_key, None)
            return None

        # ── 7. STATISTICS ──
        tier = result.get("tier", "free_api")
        is_local = result.get("is_local", False)
        source = result.get("source", "unknown")

        if is_local:
            self._stats["local_hits"] += 1
        elif tier == "free_api":
            self._stats["free_api_hits"] += 1
        else:
            self._stats["paid_api_hits"] += 1

        if result.get("fallback_used"):
            self._stats["fallback_uses"] += 1

        if source.startswith("arkham"):
            self._credit_usage["arkham"] = self._credit_usage.get("arkham", 0) + 1

        # ── 8. CACHE RESULT ──
        await self.cache.set(cache_key, result, data_type=data_type)

        # ── 9. RAG INDEXING (optional) ──
        if rag_index and result.get("data"):
            try:
                from app.integrations.rugbrain_client import rugbrain_ingest

                doc_id = f"databus:{data_type}:{hash(str(kwargs))}"
                await rugbrain_ingest(
                    content=json.dumps(result["data"], default=str)[:2000],
                    collection="databus_results",
                    doc_id=doc_id,
                    metadata={"data_type": data_type, "source": source, "tier": tier},
                )
            except (AttributeError, ImportError, json.JSONDecodeError) as e:
                logger.debug(f"fetch failed: {e}")

        # ── 10. WEBSOCKET BROADCAST ──
        if data_type in (
            "token_price",
            "alerts",
            "entity_intel",
            "smart_money",
            "market_overview",
            "trending",
            "market_movers",
        ):
            try:
                from app.databus.ws_stream import databus_ws_broadcast

                await databus_ws_broadcast(data_type, result)
            except (AttributeError, ImportError) as e:
                logger.debug(f"fetch failed: {e}")

        # ── 11. RESOLVE DEDUP ──
        self._dedup.resolve(dedup_key, result)

        # ── 12. ACCESS CONTROL - Package response ──
        consumer = access_controller.identify_consumer(
            request=request,
            admin_key=admin_key,
            consumer_type=consumer_type,
            tool_id=tool_id,
            x402_tier=x402_tier,
            service_key=service_key,
        )

        if "data" in result and isinstance(result["data"], dict):
            result["data"] = access_controller.package_response(
                result["data"],
                data_type,
                consumer,
                tool_id=tool_id,
                x402_tier=x402_tier,
            )

        result["consumer"] = consumer.value
        result["packaging"] = access_controller.check_access(data_type, consumer)

        return result

    async def _local_precheck(self, data_type: str, **kwargs) -> dict[str, Any] | None:
        """Check our own databases BEFORE calling external APIs.

        Priority order: RAG → Scanner → Wallet Labels → Redis price cache.
        All of these are FREE — we NEVER pay for data we already have.

        Returns formatted result dict if found, None if we need external APIs.
        """
        address = (
            kwargs.get("address", "") or kwargs.get("mint", "") or kwargs.get("contract", "") or kwargs.get("token", "")
        )
        query = kwargs.get("query", "")
        chain = kwargs.get("chain", "") or kwargs.get("network", "")

        try:
            # ── WALLET LABELS: ClickHouse (~154k curated) → Redis (hot cache) ──
            if data_type in ("wallet_labels", "entity_intel") and address:
                # Layer 1: ClickHouse — ~154k curated labels, first-class source
                try:
                    from app.domains.labels.sources.clickhouse_labels import (
                        query_clickhouse_labels,
                    )

                    ch_labels = await query_clickhouse_labels(address, chain or "ethereum")
                    if ch_labels:
                        label_dicts = [
                            {
                                "source": "clickhouse_wallet_memory",
                                "label": lbl.label_value,
                                "category": lbl.label_type,
                                "confidence": lbl.confidence,
                                "chain": lbl.chain,
                            }
                            for lbl in ch_labels
                        ]
                        # Write-through to Redis cache
                        try:
                            r = get_redis()
                            if r is not None:
                                for label_d in label_dicts:
                                    lc = label_d.get("chain", chain or "ethereum")
                                    r.setex(
                                        f"rmi:label:{lc}:{address}",
                                        3600,
                                        json.dumps(label_d),
                                    )
                        except (
                            ValueError,
                            ConnectionError,
                            TimeoutError,
                            RuntimeError,
                        ):
                            pass

                        return {
                            "data": {
                                "labels": label_dicts,
                                "address": address,
                                "source": "clickhouse_wallet_memory",
                                "total": "~154k curated",
                            },
                            "source": "clickhouse_wallet_memory",
                            "tier": "local",
                            "is_local": True,
                            "cached": False,
                        }
                except (
                    ValueError,
                    ConnectionError,
                    TimeoutError,
                    RuntimeError,
                    ImportError,
                ):
                    pass

                # Layer 2: Redis hot cache — frequently-accessed labels
                try:
                    r = get_redis()
                    if r is not None:
                        for c in [
                            "solana",
                            "ethereum",
                            "bsc",
                            "base",
                            "arbitrum",
                            "optimism",
                            "polygon",
                        ]:
                            data = r.get(f"rmi:label:{c}:{address}")
                            if data:
                                return {
                                    "data": {
                                        "labels": [json.loads(data)],
                                        "address": address,
                                        "source": "wallet_memory_bank",
                                        "total": "~154k curated",
                                    },
                                    "source": "wallet_memory_bank",
                                    "tier": "local",
                                    "is_local": True,
                                    "cached": True,
                                }
                except (
                    ConnectionError,
                    OSError,
                    TimeoutError,
                    httpx.HTTPError,
                    httpx.TimeoutException,
                ):
                    pass

            # ── SCANNER: check SENTINEL token security cache ──
            if (
                data_type
                in (
                    "scanner",
                    "token_price",
                    "holder_data",
                )
                and address
            ):
                try:
                    r = get_redis()
                    if r is not None:
                        scan_key = f"sentinelscan:{address}" if not chain else f"sentinelscan:{chain}:{address}"
                        cached = r.get(scan_key)
                        if cached:
                            return {
                                "data": json.loads(cached),
                                "source": "sentinel_scanner",
                                "tier": "local",
                                "is_local": True,
                                "cached": True,
                            }
                except (
                    ConnectionError,
                    OSError,
                    TimeoutError,
                    httpx.HTTPError,
                    httpx.TimeoutException,
                ):
                    pass

            # ── RAG: semantic search our 17K+ scam docs ──
            if data_type in ("rag_search", "scanner", "wallet_labels") and (query or address):
                try:
                    from app.config import settings

                    search_query = query or address
                    async with httpx.AsyncClient(timeout=10) as client:
                        rag_resp = await client.get(
                            "http://rugbrain-api:8012/api/v1/search",
                            params={
                                "q": search_query,
                                "collection": "known_scams",
                                "limit": 5,
                            },
                            headers={"X-API-Key": settings.rmi_rag_api_key},
                        )
                        if rag_resp.status_code == 200:
                            data = rag_resp.json()
                            if data.get("results") and len(data["results"]) > 0:
                                return {
                                    "data": data,
                                    "source": "local_rag",
                                    "tier": "local",
                                    "is_local": True,
                                    "cached": True,
                                }
                except (
                    ConnectionError,
                    OSError,
                    TimeoutError,
                    httpx.HTTPError,
                    httpx.TimeoutException,
                ):
                    pass

            # ── PRICE: check Redis price cache ──
            if data_type == "token_price" and address:
                try:
                    r = get_redis()
                    if r is not None:
                        cached = r.get(f"price:{address.lower()}")
                        if cached:
                            return {
                                "data": json.loads(cached),
                                "source": "local_price_cache",
                                "tier": "local",
                                "is_local": True,
                                "cached": True,
                            }
                except (
                    ConnectionError,
                    OSError,
                    TimeoutError,
                    httpx.HTTPError,
                    httpx.TimeoutException,
                ):
                    pass

        except (
            ConnectionError,
            OSError,
            TimeoutError,
            httpx.HTTPError,
            httpx.TimeoutException,
        ):
            pass

        return None

    async def fetch_batch(self, requests: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
        """Fetch multiple data types in parallel."""
        tasks = [self.fetch(**req) for req in requests]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Drop exceptions — callers expect dicts only
        return [r for r in results if not isinstance(r, BaseException)]

    async def health(self) -> dict[str, Any]:
        """Full system health check with per-type cache stats and provider health."""
        cache_health = await self.cache.health()
        vault_status = self.vault.status() if self.vault else {"error": "not loaded"}
        uptime = time.time() - self._stats["start_time"]

        type_stats = self.cache.type_stats()

        tuning_alerts = []
        for dtype, stats in type_stats.items():
            if stats.get("suggestion") == "increase_ttl":
                tuning_alerts.append(
                    {
                        "data_type": dtype,
                        "hit_rate": stats["hit_rate"],
                        "suggestion": (
                            f"Increase TTL (current {stats['current_ttl']}s, hit_rate {stats['hit_rate']}%)"
                        ),
                    }
                )
            elif stats.get("suggestion") == "decrease_ttl":
                tuning_alerts.append(
                    {
                        "data_type": dtype,
                        "hit_rate": stats["hit_rate"],
                        "suggestion": (
                            f"Decrease TTL (current {stats['current_ttl']}s, hit_rate {stats['hit_rate']}%)"
                        ),
                    }
                )

        return {
            "status": "ok" if self._initialized else "initializing",
            "uptime_seconds": round(uptime),
            "initialized": self._initialized,
            "chains": len(self.chains),
            "total_providers": sum(len(c.providers) for c in self.chains.values()),
            "stats": self._stats,
            "cache": cache_health,
            "deduplication": self._dedup.stats(),
            "provider_health": self._provider_health.all_health(),
            "cache_tuning_alerts": tuning_alerts,
            "cache_warm_running": self._warm_running,
            "vault": vault_status,
            "credit_usage": self._credit_usage,
        }

    def capacity_report(self) -> dict[str, Any]:
        """Full capacity and credit report."""
        if not self.vault:
            return {"error": "vault not loaded"}
        report = self.vault.capacity_report()
        report["own_data_sources"] = {
            "wallet_memory_bank": {"labels": "169K+", "type": "local", "cost": "free"},
            "rag": {"collections": 9, "docs": "17K+", "type": "local", "cost": "free"},
            "sentinel_scanner": {"type": "local", "cost": "free"},
            "consensus_rpc": {"providers": 5, "type": "local", "cost": "free"},
            "funding_tracer": {"type": "local", "cost": "free"},
            "price_consensus": {"sources": 4, "type": "local", "cost": "free"},
            "news_network": {"sources": "15+", "type": "local", "cost": "free"},
            "wallet_memory": {"type": "clickhouse", "cost": "free"},
        }
        report["provider_chains"] = {
            name: {
                "providers": [p.name for p in chain.providers],
                "description": chain.description,
            }
            for name, chain in self.chains.items()
        }
        return report

    def list_chains(self) -> dict[str, Any]:
        """List all available data types and their provider chains."""
        result = {}
        for name, chain in self.chains.items():
            result[name] = {
                "description": chain.description,
                "providers": [
                    {
                        "name": p.name,
                        "tier": p.tier.value,
                        "weight": p.weight,
                        "is_local": getattr(p, "is_local", p.tier == ProviderTier.LOCAL),
                        "rate_rps": getattr(p, "rate_limit_rps", p.rate_limit_rps),
                        "monthly_quota": p.monthly_quota,
                        "description": p.description,
                    }
                    for p in chain.providers
                ],
                "access_level": self.security.get_access_level(name),
            }
        return result

    async def invalidate(self, data_type: str, **kwargs) -> None:
        """Invalidate cache for a specific data type."""
        cache_key = self.cache.make_key(data_type, data_type, **kwargs)
        await self.cache.delete(cache_key)

    async def invalidate_all(self) -> None:
        """Clear the entire cache."""
        await self.cache.clear()

    def get_stats(self) -> dict[str, Any]:
        """Return fetch statistics."""
        return dict(self._stats)
