"""Receipt log extraction — fetch transaction receipt logs across EVM chains.

Primary path is the DataBus unified RPC router
(``app.domains.scanner.engines.rpc_router``), which already stacks 8-15
public/fallback endpoints per chain (LlamaNodes, Ankr, dRPC, PublicNode,
Alchemy-with-key, etc.) with health checks and circuit-breaker behavior —
that satisfies "reuse DataBus providers" without duplicating endpoint lists.

Fallback stack for chains/endpoints the router cannot serve:
  1. Etherscan V2 proxy API (``eth_getTransactionReceipt``) — free key env
     ``ETHERSCAN_API_KEY`` (already provisioned in the fleet).
  2. Alchemy connector (``app.alchemy_connector``) when ``ALCHEMY_API_KEY``
     is set.

Optional enhancement: ``fetch_debug_trace`` issues ``debug_traceTransaction``
with the callTracer via the RPC router. Only paid endpoints (Alchemy/QuickNode)
support it; the router skips public nodes that reject it — so it is best-effort
and never required for the core pipeline.
"""

from __future__ import annotations

import contextlib
from typing import Any

import httpx

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.engines.rpc_router import get_rpc_router

logger = get_logger(__name__)

# Chain → Etherscan V2 chainid (fallback provider). Only chains Etherscan
# supports are listed; everything else relies on the RPC router.
_ETHERSCAN_CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "optimism": 10,
    "base": 8453,
    "avalanche": 43114,
    "fantom": 250,
    "linea": 59144,
    "zksync": 324,
    "scroll": 534352,
    "mantle": 5000,
    "gnosis": 100,
}

_ETHERSCAN_V2_API = "https://api.etherscan.io/v2/api"
_REQUEST_TIMEOUT = 10.0

# EVM chains the RPC router serves (subset of EVM_RPC_ENDPOINTS keys).
_SUPPORTED_CHAINS = frozenset(
    {
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "base",
        "optimism",
        "avalanche",
        "fantom",
        "linea",
        "zksync",
        "scroll",
        "mantle",
        "gnosis",
    }
)


def is_supported_chain(chain: str) -> bool:
    """True if the tracing pipeline can fetch receipt logs for this chain."""
    return chain in _SUPPORTED_CHAINS


async def fetch_tx_logs(tx_hash: str, chain: str) -> tuple[list[dict[str, Any]], str]:
    """Fetch the transaction receipt logs for a tx hash.

    Returns ``(logs, source)`` where ``source`` is "rpc", "etherscan",
    "alchemy", or "none". Each log is the raw RPC receipt-log dict
    (``address``, ``topics``, ``data``, ``logIndex``, ...).
    """
    if not is_supported_chain(chain):
        logger.warning("tracing_unsupported_chain chain=%s", chain)
        return [], "none"

    # Primary: DataBus unified RPC router (multi-endpoint fallback + health checks)
    try:
        router = get_rpc_router()
        resp = await router.call(chain, "eth_getTransactionReceipt", [tx_hash])
        if resp and isinstance(resp, dict) and resp.get("result"):
            receipt = resp["result"]
            logs = receipt.get("logs") or []
            if logs:
                return logs, "rpc"
            logger.debug("tracing_empty_receipt_logs chain=%s tx=%s", chain, tx_hash[:18])
    except Exception as e:
        logger.debug("tracing_rpc_receipt_failed chain=%s err=%s", chain, e)

    # Fallback 1: Etherscan V2 proxy
    logs = await _fetch_etherscan_logs(tx_hash, chain)
    if logs:
        return logs, "etherscan"

    # Fallback 2: Alchemy connector (only when key configured)
    logs = await _fetch_alchemy_logs(tx_hash, chain)
    if logs:
        return logs, "alchemy"

    return [], "none"


async def _fetch_etherscan_logs(tx_hash: str, chain: str) -> list[dict[str, Any]]:
    """Fetch receipt via Etherscan V2 proxy API (free tier, API key from env)."""
    import os

    from app.config import settings

    api_key = settings.etherscan_api_key or os.getenv("ETHERSCAN_API_KEY", "")
    chain_id = _ETHERSCAN_CHAIN_IDS.get(chain)
    if not api_key or chain_id is None:
        return []
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(
                _ETHERSCAN_V2_API,
                params={
                    "chainid": chain_id,
                    "module": "proxy",
                    "action": "eth_getTransactionReceipt",
                    "txhash": tx_hash,
                    "apikey": api_key,
                },
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if data.get("status") == "0":
                return []
            result = data.get("result") or {}
            if isinstance(result, str):  # error message returned as string
                return []
            return result.get("logs") or []
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError) as e:
        logger.debug("tracing_etherscan_failed chain=%s err=%s", chain, e)
        return []


async def _fetch_alchemy_logs(tx_hash: str, chain: str) -> list[dict[str, Any]]:
    """Fetch receipt via the Alchemy connector (keyed network endpoint)."""
    network = {
        "ethereum": "eth",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "optimism": "optimism",
        "base": "base",
    }.get(chain)
    if network is None:
        return []
    try:
        from app.alchemy_connector import AlchemyConnector

        connector = AlchemyConnector()
        receipt = await connector.get_transaction_receipt(tx_hash, network=network)
        if receipt and isinstance(receipt, dict):
            return receipt.get("logs") or []
    except Exception as e:
        logger.debug("tracing_alchemy_failed chain=%s err=%s", chain, e)
    return []


async def fetch_debug_trace(tx_hash: str, chain: str) -> dict[str, Any] | None:
    """Best-effort ``debug_traceTransaction`` (callTracer) — optional enhancement.

    Only paid endpoints (Alchemy/QuickNode on paid tiers) support this; the
    RPC router health-check/fallback skips endpoints that reject the method.
    Returns the raw trace result dict or None.
    """
    if not is_supported_chain(chain):
        return None
    try:
        router = get_rpc_router()
        resp = await router.call(
            chain,
            "debug_traceTransaction",
            [tx_hash, {"tracer": "callTracer"}],
        )
        if resp and isinstance(resp, dict) and resp.get("result"):
            result_value = resp["result"]
            if isinstance(result_value, dict):
                return result_value
    except Exception as e:
        logger.debug("tracing_debug_trace_failed chain=%s err=%s", chain, e)
    return None


def extract_trace_hops(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract wallet→wallet hops from a callTracer trace result.

    Recursively walks ``calls`` (internal CALL/DELEGATECALL/STATICCALL frames)
    and records from/to/value hops. Returns a list of hop dicts with
    ``from``, ``to``, ``value`` (decimal string), ``token`` ("" for native).
    """
    hops: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        if not isinstance(node, dict):
            return
        from_addr = node.get("from")
        to_addr = node.get("to")
        value = node.get("value") or "0x0"
        if from_addr and to_addr and isinstance(value, str) and value != "0x0":
            with contextlib.suppress(ValueError, TypeError):
                hops.append(
                    {
                        "from": from_addr.lower(),
                        "to": to_addr.lower(),
                        "value": str(int(value, 16)),
                        "token": "",
                        "event": "internal_call",
                        "log_index": None,
                    }
                )
        for child in node.get("calls") or []:
            walk(child)

    walk(trace)
    return hops
