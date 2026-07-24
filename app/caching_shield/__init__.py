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
    BATCH_WINDOW_MS,  # noqa: F401
    MAX_BATCH_SIZE,  # noqa: F401
    BatchRequest,  # noqa: F401
    BatchResult,  # noqa: F401
    RpcBatcher,  # noqa: F401
)
from app.caching_shield.funding_tracer import (
    FundingTrace,
    trace_funding_source,
)
from app.caching_shield.history_depth import (
    DEFAULT_DEPTH,  # noqa: F401
    MAX_DEPTH,  # noqa: F401
    MAX_PAGINATED,  # noqa: F401
    HistoryDepthController,  # noqa: F401
    get_history_controller,  # noqa: F401
)
from app.caching_shield.rate_limiter import (
    PROVIDER_LIMITS,  # noqa: F401
    ProviderLimit,  # noqa: F401
    RpcRateLimiter,  # noqa: F401
    get_rate_limiter,  # noqa: F401
)
from app.caching_shield.rpc_cache import (
    TTL_TABLE,  # noqa: F401
    CacheStats,  # noqa: F401
    RpcCacheClient,  # noqa: F401
    get_rpc_cache,  # noqa: F401
)
from app.caching_shield.solana_tracker import (
    SolanaTrackerClient,  # noqa: F401
    get_solana_tracker,  # noqa: F401
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
    CHANNEL_ALERTS,  # noqa: F401
    CHANNEL_PRICES,  # noqa: F401
    CHANNEL_SCANS,  # noqa: F401
    CHANNEL_TOKENS,  # noqa: F401
    WsClientManager,  # noqa: F401
    get_ws_manager,  # noqa: F401
)

__all__ = [
    "PROVIDER_REGISTRY",
    "ApiKey",
    "FundingTrace",
    "KeyPool",
    "ProviderConfig",
    "ToolData",
    "ToolResult",
    "UnifiedApiManager",
    "UnifiedDataLayer",
    "get_api_manager",
    "get_data_layer",
    "td",
    "trace_funding_source",
]
