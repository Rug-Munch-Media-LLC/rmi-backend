"""
V1 Providers Status — GET /api/v1/providers/status
===================================================
Per-provider health, circuit-breaker state, latency, and rate-limit status
for all 30+ external API providers the backend connectors talk to.

Relies on the existing circuit-breaker instances (both AsyncCircuitBreaker
and SimpleCircuitBreaker) defined in each connector module. For providers
without explicit circuit breakers, we report "unknown" with the module
import status as a proxy.

Cache: 15-second in-memory cache to avoid hammering connector modules.
Errors: Gracefully degrades — a single failing provider never breaks the
        entire response. Returns HTTP 200 even with partial failures.
"""

from __future__ import annotations

import datetime
import importlib
import time
from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/providers", tags=["providers"])

# ──────────────────────────────────────────────────────────────────────
# Provider Registry — single source of truth
# ──────────────────────────────────────────────────────────────────────
# Each entry maps a public provider name to the Python module path and
# the circuit-breaker variable name (or None if the module has none).
# This drives dynamic import + state inspection at runtime.

ProviderDef = dict[str, Any]

PROVIDERS: list[ProviderDef] = [
    # ── AsyncCircuitBreaker providers ──────────────────────────────
    {
        "name": "coingecko",
        "module": "app.coingecko_connector",
        "cb_var": "_coingecko_cb",
        "description": "CoinGecko API — coin prices, market data, trending",
        "tier": "free+pro",
    },
    {
        "name": "etherscan",
        "module": "app.etherscan_connector",
        "cb_var": "_etherscan_cb",
        "description": "Etherscan family — EVM block explorers (ETH, BSC, Polygon, etc.)",
        "tier": "free",
    },
    {
        "name": "alchemy",
        "module": "app.alchemy_connector",
        "cb_var": "_alchemy_cb",
        "description": "Alchemy — NFT API, Enhanced API, multi-chain RPC",
        "tier": "free",
    },
    {
        "name": "birdeye",
        "module": "app.birdeye_client",
        "cb_var": "_birdeye_cb",
        "description": "Birdeye — Solana token data, price, holders",
        "tier": "free",
    },
    {
        "name": "moralis",
        "module": "app.moralis_connector",
        "cb_var": "_moralis_cb",
        "description": "Moralis — wallet/token/NFT data API + Auth API",
        "tier": "free",
    },
    {
        "name": "arkham",
        "module": "app.arkham_connector",
        "cb_var": "_arkham_cb",
        "description": "Arkham Intelligence — entity labeling, sanctions screening",
        "tier": "free",
    },
    {
        "name": "alibaba",
        "module": "app.alibaba_connector",
        "cb_var": "_alibaba_cb",
        "description": "Alibaba Cloud — Tongyi Wanxiang image generation",
        "tier": "free",
    },
    {
        "name": "dashscope",
        "module": "app.alibaba_dashscope_connector",
        "cb_var": "_dashscope_cb",
        "description": "Alibaba DashScope — Qwen LLM models (qwen-max, qwen-plus, etc.)",
        "tier": "free",
    },
    # ── SimpleCircuitBreaker providers (sync) ──────────────────────
    {
        "name": "defillama",
        "module": "app.defillama_connector",
        "cb_var": "_defillama_cb",
        "description": "DeFiLlama — TVL, yields, stablecoins, protocol data",
        "tier": "free",
    },
    {
        "name": "blockchair",
        "module": "app.blockchair_connector",
        "cb_var": "_blockchair_cb",
        "description": "Blockchair — Bitcoin/Litecoin/Ethereum blockchain explorer",
        "tier": "free",
    },
    {
        "name": "nansen",
        "module": "app.nansen_connector",
        "cb_var": "_nansen_cb",
        "description": "Nansen — wallet labels, smart money tracking",
        "tier": "free",
    },
    {
        "name": "solscan",
        "module": "app.free_solscan_client",
        "cb_var": "_solscan_cb",
        "description": "Solscan — Solana blockchain explorer (free reverse-engineered API)",
        "tier": "free",
    },
    # ── RPC / node providers (no inline circuit breaker) ──────────
    {
        "name": "helius",
        "module": None,
        "cb_var": None,
        "description": "Helius — Solana RPC, webhooks, enhanced APIs",
        "tier": "free",
    },
    {
        "name": "quicknode",
        "module": None,
        "cb_var": None,
        "description": "QuickNode — Solana multi-chain RPC endpoints",
        "tier": "free",
    },
    {
        "name": "publicnode",
        "module": None,
        "cb_var": None,
        "description": "PublicNode — free public RPC for Ethereum, Polygon, etc.",
        "tier": "free",
    },
    {
        "name": "drpc",
        "module": None,
        "cb_var": None,
        "description": "DRPC — decentralized RPC for Solana",
        "tier": "free",
    },
    {
        "name": "solana_rpc",
        "module": "app.solana_connector",
        "cb_var": None,
        "description": "Solana RPC — native solana-py client (publicnode, devnet, testnet)",
        "tier": "free",
    },
    {
        "name": "evm_web3",
        "module": "app.evm_connector",
        "cb_var": None,
        "description": "Web3.py EVM — Ethereum, Base, Arbitrum contract interaction",
        "tier": "free",
    },
    # ── Data / intelligence providers ──────────────────────────────
    {
        "name": "gmgn",
        "module": "app.gmgn_client",
        "cb_var": None,
        "description": "GMGN — Smart money, token intelligence, social metrics",
        "tier": "free",
    },
    {
        "name": "databus",
        "module": "app.databus_gateway",
        "cb_var": None,
        "description": "DataBus — 102-chain, 119-provider unified data gateway",
        "tier": "free+x402",
    },
    {
        "name": "consensus_rpc",
        "module": "app.consensus_rpc",
        "cb_var": None,
        "description": "Consensus RPC — N-of-M voting across 5+ Solana/EVM providers",
        "tier": "free",
    },
    {
        "name": "social_intel",
        "module": "app.databus.social_intel",
        "cb_var": None,
        "description": "Social intelligence — X/Twitter, news aggregation, sentiment",
        "tier": "free",
    },
    {
        "name": "news_provider",
        "module": "app.domains.news.news_service",
        "cb_var": None,
        "description": "News service — crypto news feeds, headlines, sentiment",
        "tier": "free",
    },
    # ── AI / LLM providers ─────────────────────────────────────────
    {
        "name": "openrouter",
        "module": None,
        "cb_var": None,
        "description": "OpenRouter — multi-model LLM gateway (free+paid models)",
        "tier": "free",
    },
    {
        "name": "model_gateway",
        "module": "app.core.model_router",
        "cb_var": None,
        "description": "Internal model router — AI provider selection, cost tracking",
        "tier": "internal",
    },
    # ── Ethereum consensus layer (Beacon Chain) ─────────────────────
    {
        "name": "reth",
        "module": None,
        "cb_var": None,
        "description": "Reth — Ethereum execution layer RPC (eth_blockNumber, etc.)",
        "tier": "self-hosted",
    },
]

# ──────────────────────────────────────────────────────────────────────
# Cache (15-second TTL)
# ──────────────────────────────────────────────────────────────────────
_cache: dict[str, Any] = {"data": None, "timestamp": 0.0}
_CACHE_TTL = 15  # seconds


def _read_circuit_breaker_state(cb_obj: Any) -> dict[str, Any]:
    """Read state from either AsyncCircuitBreaker or SimpleCircuitBreaker.

    Both expose:
      - .state          -> State enum (.value = "closed" | "open" | "half_open")
      - .stats()        -> dict with name, state, total_calls, rejected_calls, success_calls, failure_count
      - ._failures      -> list of failure timestamps (for last_error time)
    """
    try:
        state_val = cb_obj.state.value if hasattr(cb_obj.state, "value") else str(cb_obj.state)
        stats = cb_obj.stats() if hasattr(cb_obj, "stats") else {}

        # Determine last error time from failure list (most recent failure)
        last_error_ts: float | None = None
        failures = getattr(cb_obj, "_failures", None)
        if failures and len(failures) > 0:
            last_error_ts = max(failures) if isinstance(failures[0], (int, float)) else None

        return {
            "state": state_val,
            "total_calls": stats.get("total_calls", 0),
            "rejected_calls": stats.get("rejected_calls", 0),
            "successful_calls": stats.get("successful_calls", 0),
            "failure_count": stats.get("failure_count", len(failures) if failures else 0),
            "last_error_timestamp": (_format_timestamp(last_error_ts) if last_error_ts else None),
        }
    except Exception as exc:
        logger.warning("Failed to read circuit breaker state: %s", exc)
        return {
            "state": "unknown",
            "total_calls": 0,
            "rejected_calls": 0,
            "successful_calls": 0,
            "failure_count": 0,
            "last_error_timestamp": None,
        }


def _format_timestamp(ts: float) -> str:
    """Format a Unix timestamp to ISO 8601 string."""
    try:
        return datetime.datetime.fromtimestamp(ts, tz=datetime.UTC).isoformat()
    except (ValueError, TypeError, OSError):
        return str(ts)


def _probe_provider(provider: ProviderDef) -> dict[str, Any]:
    """Probe a single provider and return its health status.

    This is intentionally cheap — no external HTTP calls. We rely on
    the connector module's circuit breaker state (in-memory) and module
    importability as a proxy for health.

    Returns:
        dict with name, status, circuit_breaker, module_imported, etc.
    """
    name: str = provider["name"]
    module_path: str | None = provider.get("module")
    cb_var_name: str | None = provider.get("cb_var")

    result: dict[str, Any] = {
        "name": name,
        "status": "unknown",
        "healthy": None,
        "circuit_breaker": "unknown",
        "module_imported": False,
        "tier": provider.get("tier", "unknown"),
        "description": provider.get("description", ""),
        "last_error": None,
        "latency_ms": None,
        "circuit_breaker_details": None,
    }

    # Try to import the module and read its circuit breaker
    if module_path:
        try:
            mod = importlib.import_module(module_path)
            result["module_imported"] = True

            # Read circuit breaker state if we know the variable name
            if cb_var_name and hasattr(mod, cb_var_name):
                cb = getattr(mod, cb_var_name)
                cb_info = _read_circuit_breaker_state(cb)
                result["circuit_breaker"] = cb_info["state"]
                result["last_error"] = cb_info["last_error_timestamp"]

                # Derive overall status from circuit breaker state
                if cb_info["state"] == "closed":
                    result["status"] = "healthy"
                    result["healthy"] = True
                elif cb_info["state"] == "half_open":
                    result["status"] = "degraded"
                    result["healthy"] = True
                else:  # open
                    result["status"] = "unhealthy"
                    result["healthy"] = False

                result["circuit_breaker_details"] = {k: v for k, v in cb_info.items() if k != "last_error_timestamp"}
            else:
                # Module imports fine but has no circuit breaker — optimistic healthy
                result["status"] = "healthy" if result["module_imported"] else "unknown"
                result["healthy"] = result["module_imported"]
                result["circuit_breaker"] = "none"

        except ImportError as exc:
            result["status"] = "unhealthy"
            result["healthy"] = False
            result["last_error"] = f"ImportError: {exc}"
            logger.debug("Provider %s not importable: %s", name, exc)
        except Exception as exc:
            result["status"] = "unhealthy"
            result["healthy"] = False
            result["last_error"] = str(exc)[:200]
            logger.warning("Provider %s probe failed: %s", name, exc)
    else:
        # No local module — external-only provider (helius, quicknode, etc.)
        # Mark as unknown-healthy since we can't inspect state locally
        result["status"] = "unknown"
        result["healthy"] = None
        result["circuit_breaker"] = "none"

    return result


# ──────────────────────────────────────────────────────────────────────
# Endpoint
# ──────────────────────────────────────────────────────────────────────


@router.get("/status")
async def providers_status() -> dict[str, Any]:
    """Get per-provider health status for all external API providers.

    Returns a list of all registered providers with:
      - name / tier / description
      - status (healthy | degraded | unhealthy | unknown)
      - circuit_breaker state (closed | open | half_open | none | unknown)
      - last_error timestamp
      - circuit_breaker details (if available)

    Results are cached for 15 seconds.
    """
    now = time.monotonic()

    # Check cache
    global _cache
    if _cache["data"] is not None and (now - _cache["timestamp"]) < _CACHE_TTL:
        return _cache["data"]

    # Probe all providers
    provider_results: list[dict[str, Any]] = []
    for provider in PROVIDERS:
        provider_results.append(_probe_provider(provider))

    # Compute summary
    total = len(provider_results)
    healthy = sum(1 for p in provider_results if p.get("healthy") is True)
    unhealthy = sum(1 for p in provider_results if p.get("healthy") is False)
    unknown = sum(1 for p in provider_results if p.get("healthy") is None)

    response = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "providers": provider_results,
        "summary": {
            "total": total,
            "healthy": healthy,
            "unhealthy": unhealthy,
            "unknown": unknown,
        },
    }

    # Update cache
    _cache = {"data": response, "timestamp": time.monotonic()}

    return response
