"""ClickHouse client, pair cache, chain normalization, and cache management."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from app.core.chain_names import normalize_chain
from app.core.threads import run_in_thread
from app.domains.scanner.engines._ch_registry import thread_client
from app.domains.scanner.engines._logging import get_logger

# Backwards-compatible alias: callers import _normalize_chain from here.
_normalize_chain = normalize_chain

logger = get_logger(__name__)

# ── DexScreener-compatible response cache ──
_DEX_CACHE: dict[str, dict[str, Any] | None] = {}
_DEX_CACHE_MAX = 100

# ── Lazy ClickHouse client ──
_ch_client: Any = None
_ch_pair_cache: dict[str, dict[str, tuple[str, str]]] = {}

# Pair cache directory (from lite_indexer)
_PAIR_CACHE_DIR = Path(os.getenv("PAIR_CACHE_DIR", "/var/lib/chain-indexer/state"))


def _load_pair_cache(chain: str) -> dict[str, tuple[str, str]]:
    """Load the pair→tokens cache for a chain.

    Built by lite_indexer from PairCreated/PoolCreated events + eth_call fallback.
    Allows resolving existing dex_events by pool address (contract_address)
    since all 47M+ historical events have empty token0/token1.
    """
    global _ch_pair_cache
    if chain in _ch_pair_cache:
        return _ch_pair_cache[chain]

    cache_path = _PAIR_CACHE_DIR / f"pair_tokens_{chain}.json"
    if not cache_path.exists():
        _ch_pair_cache[chain] = {}
        logger.debug("pair_cache_not_found", chain=chain)
        return {}

    try:
        data = json.loads(cache_path.read_text())
        result = {k: (v[0], v[1]) if isinstance(v, list) else v for k, v in data.items()}
        _ch_pair_cache[chain] = result
        logger.debug("pair_cache_loaded", chain=chain, pairs=len(result))
        return result
    except (json.JSONDecodeError, OSError, TypeError) as exc:
        logger.warning("pair_cache_load_failed", chain=chain, error=str(exc)[:80])
        _ch_pair_cache[chain] = {}
        return {}


def _get_pools_for_token(pair_cache: dict[str, tuple[str, str]], token_address: str) -> list[str]:
    """Reverse-lookup: find all pool addresses where token0 or token1 matches."""
    token_lower = token_address.lower()
    pools: list[str] = []
    for pool_addr, (t0, t1) in pair_cache.items():
        if t0.lower() == token_lower or t1.lower() == token_lower:
            pools.append(pool_addr)
    return pools


def _get_ch_client() -> Any | None:
    """Return this thread's ClickHouse client (the sync driver is not thread-safe)."""
    global _ch_client
    client = thread_client("own_dex_data")
    if client is not None and threading.current_thread() is threading.main_thread():
        _ch_client = client
    return client


async def _run_sync(fn, *args, **kwargs):
    """Run a sync function in a thread pool (clickhouse_driver is sync)."""

    return await run_in_thread(fn, *args, **kwargs)


# ── Chain name normalization ──




# ── Metrics ──


def get_cache_stats() -> dict[str, Any]:
    """Return current cache statistics for monitoring."""
    return {
        "cache_size": len(_DEX_CACHE),
        "cache_max": _DEX_CACHE_MAX,
        "clickhouse_connected": _ch_client is not None,
    }


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _DEX_CACHE.clear()
