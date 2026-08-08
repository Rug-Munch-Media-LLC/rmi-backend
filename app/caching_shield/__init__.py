"""
Aggressive Caching Shield - Multi-Layer API Protection for Free RPC Tiers

Protects free tier RPC API keys (Helius, QuickNode, Alchemy) from
exhaustion by frontend traffic. Enforces cache-first architecture:

  1. RpcCacheClient - Redis L2 + in-memory L1 cache with TTL tiers
  2. RpcRateLimiter - Token bucket rate limiting per provider
  3. RpcBatcher - JSON-RPC batch request grouper (reduces call count)
  4. HistoryDepthController - Caps default query depth, gates deep scans
  5. WsClientManager - Connection-pooled Redis pub/sub for live streams

All modules fall back gracefully if Redis is unavailable.

Usage:
    from app.caching_shield import (
        get_rpc_cache,
        get_rate_limiter,
        get_ws_manager,
        get_history_controller,
    )

    # Cache-first RPC query
    cache = get_rpc_cache()
    result = await cache.get_balance("SoL...")

    # Rate-limited via token bucket
    limiter = get_rate_limiter()
    allowed, wait = await limiter.acquire("helius", "getBalance")
    if not allowed:
        raise HTTPException(429, f"Rate limited, retry in {wait:.1f}s")

    # Broadcast to WebSocket stream
    ws = get_ws_manager()
    await ws.broadcast_scan({"token": "SoL...", "safety_score": 85})

    # Clamp query depth
    hdc = get_history_controller()
    limit = hdc.clamp_limit(100, is_deep_scan=True)
"""

from __future__ import annotations

from app.caching_shield.api_registry import (
    PROVIDER_REGISTRY,
    ApiKey,
    KeyPool,
    ProviderConfig,
    UnifiedApiManager,
    get_api_manager,
)
from app.caching_shield.batcher import (
    BATCH_WINDOW_MS,
    MAX_BATCH_SIZE,
    BatchRequest,
    BatchResult,
    RpcBatcher,
)
from app.caching_shield.funding_tracer import (
    FundingTrace,
    trace_funding_source,
)
from app.caching_shield.history_depth import (
    DEFAULT_DEPTH,
    MAX_DEPTH,
    MAX_PAGINATED,
    HistoryDepthController,
    get_history_controller,
)

# AUDIT-2026-Q3 P0 (rate-limiter consolidation): RpcRateLimiter DISABLED.
# UnifiedRateLimiter (app.core.rate_limiter) is the single active limiter and
# provides acquire_provider() for backward compat. This deprecated per-provider
# limiter is kept for reference only — see app/caching_shield/rate_limiter.py.
# from app.caching_shield.rate_limiter import (
#     PROVIDER_LIMITS,
#     ProviderLimit,
#     RpcRateLimiter,
#     get_rate_limiter,
# )
from app.caching_shield.rpc_cache import (
    TTL_TABLE,
    CacheStats,
    RpcCacheClient,
    get_rpc_cache,
)
from app.caching_shield.solana_tracker import (
    SolanaTrackerClient,
    get_solana_tracker,
)
from app.caching_shield.tool_data import (
    ToolData,
    td,
)
from app.caching_shield.unified_layer import (
    ToolResult,
    UnifiedDataLayer,
    get_data_layer,
)
from app.caching_shield.ws_broadcaster import (
    CHANNEL_ALERTS,
    CHANNEL_PRICES,
    CHANNEL_SCANS,
    CHANNEL_TOKENS,
    WsClientManager,
    get_ws_manager,
)

__all__ = [
    "BATCH_WINDOW_MS",
    "CHANNEL_ALERTS",
    "CHANNEL_PRICES",
    "CHANNEL_SCANS",
    "CHANNEL_TOKENS",
    "DEFAULT_DEPTH",
    "MAX_BATCH_SIZE",
    "MAX_DEPTH",
    "MAX_PAGINATED",
    # "PROVIDER_LIMITS",  # DISABLED AUDIT-2026-Q3 P0 — RpcRateLimiter retired; UnifiedRateLimiter
    "PROVIDER_REGISTRY",
    "TTL_TABLE",
    "ApiKey",
    "BatchRequest",
    "BatchResult",
    "CacheStats",
    "FundingTrace",
    "HistoryDepthController",
    "KeyPool",
    "ProviderConfig",
    # "ProviderLimit",  # DISABLED AUDIT-2026-Q3 P0 — RpcRateLimiter retired; UnifiedRateLimiter
    "RpcBatcher",
    "RpcCacheClient",
    # "RpcRateLimiter",  # DISABLED AUDIT-2026-Q3 P0 — RpcRateLimiter retired; UnifiedRateLimiter
    "SolanaTrackerClient",
    "ToolData",
    "ToolResult",
    "UnifiedApiManager",
    "UnifiedDataLayer",
    "WsClientManager",
    "get_api_manager",
    "get_data_layer",
    "get_history_controller",
    # "get_rate_limiter",  # DISABLED AUDIT-2026-Q3 P0 — RpcRateLimiter retired; UnifiedRateLimiter
    "get_rpc_cache",
    "get_solana_tracker",
    "get_ws_manager",
    "td",
    "trace_funding_source",
]
