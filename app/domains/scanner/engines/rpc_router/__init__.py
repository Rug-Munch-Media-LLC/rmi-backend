"""
Unified RPC Router — Multi-Endpoint Fallback with Health Checks
===============================================================
Every RPC call routes through this package. No file hardcodes its own URLs.
If one endpoint is down/rate-limited/slow, we skip to the next automatically.

Architecture:
    rpc_router/            ← YOU ARE HERE — unified endpoint registry + fallback
        evm.py             ← EVM endpoint registry + EVM conveniences
        solana.py          ← Solana endpoint registry
        hedging.py         ← endpoint health + failover/hedging logic
        helpers.py         ← shared helpers (URL resolution, tiers, quota, report)
      └─ chain_registry.py  ← canonical chain configs reference RPC lists
      └─ data_fallback.py   ← fallback chain engine, references router health
      └─ _core.py           ← scanner helpers, delegates RPC calls here
      └─ consensus_rpc.py   ← multi-provider consensus voting, references here

Pattern for callers:
    router = get_rpc_router()
    result = await router.call("ethereum", "eth_blockNumber", [])
    # → tries 10+ endpoints, returns first success, or None
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.engines.rpc_router.evm import EVM_RPC_ENDPOINTS, EvmRpcMixin
from app.domains.scanner.engines.rpc_router.hedging import EndpointHealth, HedgingMixin
from app.domains.scanner.engines.rpc_router.helpers import HelpersMixin, _init_router_state
from app.domains.scanner.engines.rpc_router.solana import SOLANA_RPC_ENDPOINTS

logger = get_logger(__name__)

__all__ = [
    "EVM_RPC_ENDPOINTS",
    "SOLANA_RPC_ENDPOINTS",
    "EndpointHealth",
    "RpcRouter",
    "get_rpc_router",
    "rpc_call",
]


class RpcRouter(HelpersMixin, HedgingMixin, EvmRpcMixin):
    """
    Unified RPC router with multi-endpoint fallback + health checks.

    Usage:
        router = get_rpc_router()
        # EVM: get latest block
        result = await router.call("ethereum", "eth_blockNumber", [])
        # Solana: get balance
        result = await router.call("solana", "getBalance", ["address..."])

    Health checks run automatically before each call — dead endpoints
    are skipped until their cooldown expires.
    """

    def __init__(self) -> None:
        _init_router_state(self)

    async def call(
        self,
        chain: str,
        method: str,
        params: list[Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """
        Make an RPC call with automatic fallback across all endpoints.

        Args:
            chain: Chain name (e.g. "ethereum", "solana", "base")
            method: JSON-RPC method (e.g. "eth_call", "getBalance")
            params: JSON-RPC params list
            timeout: Optional per-endpoint timeout override (default: 10s)

        Returns:
            RPC result dict (the "result" field), or None if all endpoints failed.
        """
        if params is None:
            params = []

        endpoints = await self.ensure_healthy_endpoints(chain)
        if not endpoints:
            logger.error("no_healthy_rpc_endpoints", chain=chain)
            return None

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }

        call_timeout = timeout or self._call_timeout
        last_error: str | None = None

        client = await self._get_client()

        for url, health in endpoints:
            if health.tier == "freemium":
                self._record_quota(url)  # keyed provider: count against hourly window
            try:
                t0 = time.monotonic()
                resp = await client.post(
                    url,
                    json=body,
                    timeout=call_timeout,
                )
                latency = (time.monotonic() - t0) * 1000

                if resp.status_code == 200:
                    data = resp.json()
                    if data and isinstance(data, dict) and "result" in data:
                        health.record_success(latency)
                        # Check for null result (e.g. empty account)
                        if data["result"] is not None:
                            return data
                        return data  # Return anyway, caller handles null
                    elif data and "error" in data:
                        error_msg = data["error"].get("message", str(data["error"]))
                        health.record_failure()
                        last_error = error_msg
                        logger.debug(
                            "rpc_json_error",
                            url=url,
                            method=method,
                            error=error_msg[:80],
                        )
                        # JSON-RPC errors are often permanent — don't retry for these
                        if data["error"].get("code") in (-32000, -32001, -32004, -32602, -32601):
                            return None
                        continue
                    else:
                        health.record_failure()
                        last_error = "empty_response"
                        continue

                elif resp.status_code == 429:
                    # Rate limited: mark dead with longer cooldown
                    health.is_dead = True
                    health.dead_until = time.time() + 120
                    health.consecutive_failures += 1
                    last_error = f"rate_limited_{resp.status_code}"
                    logger.debug("rpc_call_rate_limited", url=url, chain=chain)
                    continue

                else:
                    health.record_failure()
                    last_error = f"http_{resp.status_code}"
                    logger.debug(
                        "rpc_call_http_error",
                        url=url,
                        status=resp.status_code,
                        method=method,
                    )
                    continue

            except httpx.TimeoutException:
                health.record_failure()
                last_error = "timeout"
                logger.debug("rpc_call_timeout", url=url, method=method)
                continue

            except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
                health.record_failure()
                last_error = str(exc)[:60]
                logger.debug("rpc_call_connection_error", url=url, error=last_error)
                continue

            except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as exc:
                health.record_failure()
                last_error = str(exc)[:80]
                logger.debug("rpc_call_unexpected_error", url=url, error=last_error)
                continue

        logger.warning(
            "rpc_all_endpoints_failed",
            chain=chain,
            method=method,
            endpoints_tried=len(endpoints),
            last_error=last_error,
        )
        return None


# ══════════════════════════════════════════════════════════════════════
# GLOBAL SINGLETON
# ══════════════════════════════════════════════════════════════════════

_rpc_router: RpcRouter | None = None


def get_rpc_router() -> RpcRouter:
    """Get or create the global RpcRouter singleton."""
    global _rpc_router
    if _rpc_router is None:
        _rpc_router = RpcRouter()
        logger.info(
            "RpcRouter initialized: %d EVM chains, Solana, ~%d total endpoints",
            len(EVM_RPC_ENDPOINTS),
            sum(len(v) for v in EVM_RPC_ENDPOINTS.values()) + len(SOLANA_RPC_ENDPOINTS),
        )
    return _rpc_router


async def rpc_call(
    chain: str,
    method: str,
    params: list[Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    """
    Quick one-shot RPC call with full fallback.

    For one-off calls when you don't need the router instance.
    """
    router = get_rpc_router()
    return await router.call(chain, method, params, timeout=timeout)
