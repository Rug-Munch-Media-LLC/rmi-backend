"""
DataBus Core - THE Single Source of Truth
==========================================

Every data request in the entire platform routes through this class.
Cache(SWR) → Dedup → Provider Chain → Broadcast. All in one place.

Improvements over v1:
  - Stale-While-Revalidate: Users never wait for cache refresh
  - Request Deduplication: Same query within 5s shares one API call
  - Cache Warm: Background prefetch for hot data types
  - Provider Health: Per-provider success/failure/latency tracking
  - Per-type cache stats with TTL tuning suggestions

Usage:
    from app.databus import databus

    result = await databus.fetch("token_price", mint="So1111...")
    result = await databus.fetch("wallet_labels", address="7EcD...")
    result = await databus.fetch("entity_intel", address="0x...", admin_key="...")

    # System health
    health = await databus.health()
    capacity = databus.capacity_report()
"""

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from typing import Any

from app.core.logging import get_logger
from app.databus.access_control import access_controller
from app.databus.cache import CacheLayer, get_cache
from app.databus.providers import ProviderChain, ProviderTier, build_provider_chains
from app.databus.security import security
from app.databus.vault import DataBusVault, get_vault

logger = get_logger("databus.core")


class RequestDeduplicator:
    """Deduplicate in-flight requests for the same data.

    If two callers request token_price for the same token while a fetch
    is already in progress, the second caller awaits the same future
    instead of firing a duplicate API call.
    """

    def __init__(self) -> None:
        self._in_flight: dict[str, asyncio.Future] = {}
        self._dedup_hits = 0
        self._dedup_saved_calls = 0

    def make_key(self, data_type: str, **kwargs) -> str:
        args_str = json.dumps(kwargs, sort_keys=True, default=str)
        args_hash = hashlib.sha256(args_str.encode()).hexdigest()[:16]
        return f"{data_type}:{args_hash}"

    async def get_or_create(self, key: str) -> asyncio.Future:
        """Get existing future or create a new one. Caller must resolve it."""
        if key in self._in_flight:
            self._dedup_hits += 1
            self._dedup_saved_calls += 1
            return self._in_flight[key], True  # (future, is_duplicate)
        future = asyncio.get_event_loop().create_future()
        self._in_flight[key] = future
        return future, False  # (future, is_new)

    def resolve(self, key: str, result: Any) -> None:
        """Resolve an in-flight future and clean up."""
        future = self._in_flight.pop(key, None)
        if future and not future.done():
            future.set_result(result)

    def fail(self, key: str, exc: Exception) -> None:
        """Fail an in-flight future and clean up."""
        future = self._in_flight.pop(key, None)
        if future and not future.done():
            future.set_exception(exc)

    def stats(self) -> dict:
        return {
            "in_flight": len(self._in_flight),
            "dedup_hits": self._dedup_hits,
            "saved_api_calls": self._dedup_saved_calls,
        }


class ProviderHealthTracker:
    """Track per-provider health: success/failure rates, latency, availability."""

    def __init__(self) -> None:
        self._providers: dict[str, dict] = defaultdict(
            lambda: {
                "total_calls": 0,
                "successes": 0,
                "failures": 0,
                "consecutive_failures": 0,
                "last_success": None,
                "last_failure": None,
                "avg_latency_ms": 0,
                "is_available": True,
                "circuit_open_until": 0,  # monotonic timestamp
            }
        )

    def record_success(self, provider_name: str, latency_ms: float) -> None:
        p = self._providers[provider_name]
        p["total_calls"] += 1
        p["successes"] += 1
        p["consecutive_failures"] = 0
        p["last_success"] = time.time()
        p["is_available"] = True
        # Running average latency
        if p["avg_latency_ms"] == 0:
            p["avg_latency_ms"] = latency_ms
        else:
            p["avg_latency_ms"] = p["avg_latency_ms"] * 0.8 + latency_ms * 0.2

    def record_failure(self, provider_name: str, error: str = "") -> None:
        p = self._providers[provider_name]
        p["total_calls"] += 1
        p["failures"] += 1
        p["consecutive_failures"] += 1
        p["last_failure"] = time.time()
        p["last_error"] = error
        # Circuit breaker: 3 consecutive failures = open for 30s
        if p["consecutive_failures"] >= 3:
            p["circuit_open_until"] = time.monotonic() + 30
            p["is_available"] = False
            logger.warning(
                f"Provider '{provider_name}' circuit breaker OPEN (3 consecutive failures)"
            )

    def is_available(self, provider_name: str) -> bool:
        p = self._providers[provider_name]
        if not p["is_available"]:
            # Check if circuit breaker cooldown has passed
            if time.monotonic() > p["circuit_open_until"]:
                p["is_available"] = True  # Half-open: allow one probe
                logger.info(f"Provider '{provider_name}' circuit breaker HALF-OPEN (probing)")
                return True
            return False
        return True

    def get_health(self, provider_name: str) -> dict:
        return dict(self._providers.get(provider_name, {}))

    def all_health(self) -> dict[str, dict]:
        result = {}
        for name, data in self._providers.items():
            total = data["total_calls"]
            result[name] = {
                **data,
                "success_rate": round(data["successes"] / total * 100, 1) if total > 0 else 0,
                "circuit_open_until": data["circuit_open_until"] - time.monotonic()
                if data["circuit_open_until"] > time.monotonic()
                else 0,
            }
        return result


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
        self.vault: DataBusVault = None  # loaded async
        self.chains: dict[str, ProviderChain] = {}
        self.security = security
        self._initialized = False
        self._init_lock = asyncio.Lock()
        # New: Request deduplication
        self._dedup = RequestDeduplicator()
        # New: Provider health
        self._provider_health = ProviderHealthTracker()
        # New: Cache warm task
        self._warm_task: asyncio.Task | None = None
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
            except Exception as e:  # noqa: BLE001
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
        # Parse data_type from key format "source:data_type:hash"
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
                # Broadcast if it's a real-time type
                if data_type in ("token_price", "alerts", "entity_intel"):
                    try:
                        from app.caching_shield.ws_broadcaster import get_ws_manager

                        ws = get_ws_manager()
                        await ws.broadcast_data(data_type, result)
                    except (AttributeError, ImportError):  # noqa: BLE001
                        pass
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
        # Hot data types to prefetch - ONLY free/local endpoints to save credits
        self._warm_types = [
            {"data_type": "market_overview", "kwargs": {}},
            {"data_type": "trending", "kwargs": {}},
            {"data_type": "market_brief", "kwargs": {}},
            {"data_type": "alerts", "kwargs": {}},
            {"data_type": "news", "kwargs": {}},
            # Extended warm targets for top-10 most-missed chains
            {"data_type": "token_price", "kwargs": {}},
            {"data_type": "token_metadata", "kwargs": {}},
            {"data_type": "holder_data", "kwargs": {}},
            {"data_type": "wallet_labels", "kwargs": {}},
            {"data_type": "wallet_tokens", "kwargs": {}},
            {"data_type": "live_prices", "kwargs": {}},
        ]
        self._warm_task = asyncio.create_task(self._warm_loop(interval_seconds))
        logger.info(
            f"Cache warm started (interval={interval_seconds}s, {len(self._warm_types)} FREE types)"
        )

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
                    try:  # noqa: SIM105
                        await self.fetch(
                            data_type=item["data_type"],
                            force_fresh=True,
                            consumer_type="authenticated",
                            **item.get("kwargs", {}),
                        )
                    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                        pass  # Individual warm failures are fine
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                logger.warning(f"Cache warm loop error: {e}")
                await asyncio.sleep(interval)

    # ── THE Fetch Method ───────────────────────────────────────────

    async def fetch(
        self,
        data_type: str,
        admin_key: str = "",
        force_fresh: bool = False,
        rag_index: bool = False,
        consumer_type: str = "",
        tool_id: str = "",
        x402_tier: str = "",
        request=None,
        **kwargs,
    ) -> dict | None:
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
        if not self.security.check_access(data_type, admin_key=admin_key):
            consumer = access_controller.identify_consumer(
                admin_key=admin_key,
                consumer_type=consumer_type,
                tool_id=tool_id,
                x402_tier=x402_tier,
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
            return {"error": "unknown_data_type", "message": f"No provider chain for '{data_type}'"}

        # ── 4. CACHE CHECK (with SWR) ──
        cache_key = self.cache.make_key(data_type, data_type, **kwargs)
        if not force_fresh:
            cached, is_stale = await self.cache.get(cache_key, data_type=data_type)
            if cached is not None:
                if is_stale:
                    self._stats["cache_stale_hits"] += 1
                    cached["cached"] = "stale"  # User got instant data, bg refresh fires
                else:
                    self._stats["cache_hits"] += 1
                    cached["cached"] = True
                return cached

        self._stats["cache_misses"] += 1

        # ── 5. REQUEST DEDUPLICATION ──
        dedup_key = self._dedup.make_key(data_type, **kwargs)
        future, is_duplicate = await self._dedup.get_or_create(dedup_key)
        if is_duplicate:
            # Another request is already fetching this - wait for it
            self._stats["dedup_hits"] += 1
            try:
                result = await asyncio.wait_for(future, timeout=30)
                return result
            except (ConnectionError, OSError, TimeoutError):  # noqa: BLE001
                pass

        # ── 5.5 LOCAL PRECHECK - RAG / Scanner / Labels / Redis (FREE) ──
        # Before calling ANY external API, check our own databases.
        # This preserves credits and returns faster for data we already have.
        local_result = await self._local_precheck(data_type, **kwargs)
        if local_result:
            self._stats["local_hits"] += 1
            await self.cache.set(cache_key, local_result, data_type=data_type)
            self._dedup.resolve(dedup_key, local_result)
            return local_result

        # ── 6. PROVIDER CHAIN (credit-aware, with health tracking) ──
        result = None
        try:
            result = await chain.fetch(vault=self.vault, cache=self.cache, **kwargs)
            if result:
                source = result.get("source", "unknown")
                latency = result.get("latency_ms", 0)
                self._provider_health.record_success(source, latency)
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
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
                from app.rag_service import index_document

                doc_id = f"databus:{data_type}:{hash(str(kwargs))}"
                await index_document(
                    collection="databus_results",
                    doc_id=doc_id,
                    text=json.dumps(result["data"], default=str)[:2000],
                    metadata={"data_type": data_type, "source": source, "tier": tier},
                )
            except (AttributeError, ImportError, json.JSONDecodeError):  # noqa: BLE001
                pass

        # ── 10. WEBSOCKET BROADCAST (optional) ──
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
            except (AttributeError, ImportError):  # noqa: BLE001
                pass

        # ── 11. RESOLVE DEDUP ──
        self._dedup.resolve(dedup_key, result)

        # ── 12. ACCESS CONTROL - Package response based on who's asking ──
        consumer = access_controller.identify_consumer(
            request=request,
            admin_key=admin_key,
            consumer_type=consumer_type,
            tool_id=tool_id,
            x402_tier=x402_tier,
        )

        if "data" in result and isinstance(result["data"], dict):
            result["data"] = access_controller.package_response(
                result["data"], data_type, consumer, tool_id=tool_id, x402_tier=x402_tier
            )

        result["consumer"] = consumer.value
        result["packaging"] = access_controller.check_access(data_type, consumer)

        return result

    async def _local_precheck(self, data_type: str, **kwargs) -> dict | None:
        """Check our own databases BEFORE calling external APIs.

        Priority order: RAG → Scanner → Wallet Labels → Redis price cache.
        All of these are FREE - we NEVER pay for data we already have.

        Returns formatted result dict if found, None if we need external APIs.
        """
        address = (
            kwargs.get("address", "")
            or kwargs.get("mint", "")
            or kwargs.get("contract", "")
            or kwargs.get("token", "")
        )
        query = kwargs.get("query", "")
        chain = kwargs.get("chain", "") or kwargs.get("network", "")

        try:
            # ── WALLET LABELS: check our 190K database ──
            if data_type in ("wallet_labels", "entity_intel") and address:
                try:
                    import json
                    import os

                    import redis

                    r = redis.Redis(
                        host=os.getenv("REDIS_HOST", "rmi-redis"),
                        port=6379,
                        password=os.getenv("REDIS_PASSWORD", ""),
                        decode_responses=True,
                        socket_connect_timeout=2,
                    )
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
                            r.close()
                            return {
                                "data": {
                                    "labels": [json.loads(data)],
                                    "address": address,
                                    "source": "wallet_memory_bank",
                                    "total": "190K+",
                                },
                                "source": "wallet_memory_bank",
                                "tier": "local",
                                "is_local": True,
                                "cached": True,
                            }
                    r.close()
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                    pass

            # ── SCANNER: check SENTINEL token security cache ──
            if data_type in ("scanner", "token_price", "token_metadata", "holder_data") and address:
                try:
                    import json
                    import os

                    import redis

                    r = redis.Redis(
                        host=os.getenv("REDIS_HOST", "rmi-redis"),
                        port=6379,
                        password=os.getenv("REDIS_PASSWORD", ""),
                        decode_responses=True,
                        socket_connect_timeout=2,
                    )
                    scan_key = (
                        f"sentinelscan:{address}"
                        if not chain
                        else f"sentinelscan:{chain}:{address}"
                    )
                    cached = r.get(scan_key)
                    if cached:
                        r.close()
                        return {
                            "data": json.loads(cached),
                            "source": "sentinel_scanner",
                            "tier": "local",
                            "is_local": True,
                            "cached": True,
                        }
                    r.close()
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                    pass

            # ── RAG: semantic search our 17K+ scam docs ──
            if data_type in ("rag_search", "scanner", "wallet_labels") and (query or address):
                try:
                    import httpx

                    search_query = query or address
                    async with httpx.AsyncClient(timeout=10) as c:
                        r = await c.post(
                            "http://localhost:8000/api/v1/rag/search",
                            json={"query": search_query, "collection": "known_scams", "limit": 5},
                        )
                        if r.status_code == 200:
                            data = r.json()
                            if data.get("results") and len(data["results"]) > 0:
                                return {
                                    "data": data,
                                    "source": "local_rag",
                                    "tier": "local",
                                    "is_local": True,
                                    "cached": True,
                                }
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                    pass

            # ── PRICE: check Redis price cache ──
            if data_type == "token_price" and address:
                try:
                    import json
                    import os

                    import redis

                    r = redis.Redis(
                        host=os.getenv("REDIS_HOST", "rmi-redis"),
                        port=6379,
                        password=os.getenv("REDIS_PASSWORD", ""),
                        decode_responses=True,
                        socket_connect_timeout=2,
                    )
                    cached = r.get(f"price:{address.lower()}")
                    if cached:
                        r.close()
                        return {
                            "data": json.loads(cached),
                            "source": "local_price_cache",
                            "tier": "local",
                            "is_local": True,
                            "cached": True,
                        }
                    r.close()
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                    pass

        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            pass

        return None

    async def fetch_batch(self, requests: list[dict]) -> list[dict | None]:
        """Fetch multiple data types in parallel."""
        tasks = [self.fetch(**req) for req in requests]
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def health(self) -> dict:
        """Full system health check with per-type cache stats and provider health."""
        cache_health = await self.cache.health()
        vault_status = self.vault.status() if self.vault else {"error": "not loaded"}
        uptime = time.time() - self._stats["start_time"]

        # Per-type cache stats with TTL tuning suggestions
        type_stats = self.cache.type_stats()

        # Build alerts for low hit-rate types
        tuning_alerts = []
        for dtype, stats in type_stats.items():
            if stats.get("suggestion") == "increase_ttl":
                tuning_alerts.append(
                    {
                        "data_type": dtype,
                        "hit_rate": stats["hit_rate"],
                        "suggestion": f"Increase TTL (current {stats['current_ttl']}s, hit_rate {stats['hit_rate']}%)",
                    }
                )
            elif stats.get("suggestion") == "decrease_ttl":
                tuning_alerts.append(
                    {
                        "data_type": dtype,
                        "hit_rate": stats["hit_rate"],
                        "suggestion": f"Decrease TTL (current {stats['current_ttl']}s, hit_rate {stats['hit_rate']}%)",
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

    def capacity_report(self) -> dict:
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

    def list_chains(self) -> dict:
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

    def get_stats(self) -> dict:
        """Return fetch statistics."""
        return dict(self._stats)


# ── Singleton ─────────────────────────────────────────────────────────────────

databus = DataBus()
