"""Utility functions for x402 integration — catalog building, format converters,
rate limiting, payment helpers, bundle execution, alias resolution, enrichment."""

from __future__ import annotations

import asyncio
import hashlib as _hl
import json
import os
from typing import TYPE_CHECKING, Any

import aiohttp
import httpx
import requests

from app.core.logging import get_logger

from .models import CHAIN_SUFFIXES, NETWORKS

logger = get_logger("utils")

if TYPE_CHECKING:
    from fastapi import Request

# ── Catalog & Format Converters ────────────────────────────────


async def build_tools_from_catalog() -> list[dict[str, Any]]:
    """Build tool list from TOOL_PRICES (source of truth for all 70+ security engines)."""
    from app.routers.x402_enforcement import TOOL_PRICES

    tools = []
    for tool_id, pricing in sorted(TOOL_PRICES.items()):
        desc = pricing.get("description", f"{tool_id} - crypto intelligence tool")
        category = pricing.get("category", "analysis")
        chain = pricing.get("chain")
        base_tool = pricing.get("base_tool")
        tools.append(
            {
                "id": tool_id,
                "description": desc,
                "price_usd": pricing.get("price_usd", 0.01),
                "category": category,
                "chain": chain,
                "base_tool": base_tool,
                "is_variant": bool(chain),
                "trial_free": pricing.get("trial_free", 1),
            }
        )
    return tools


def _tool_desc(t: dict[str, Any]) -> str:
    """Build tool description string with price and trial info."""
    return (
        f"{t['description']} - ${t['price_usd']:.2f}/call, {t['trial_free']} free trial(s). Category: {t['category']}."
    )


def format_openai_tool(t: dict[str, Any]) -> dict[str, Any]:
    """Format a single tool for OpenAI function calling."""
    return {
        "type": "function",
        "function": {
            "name": t["id"],
            "description": _tool_desc(t),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": f"Token or wallet address to analyze with {t['id']}",
                    },
                    "chain": {
                        "type": "string",
                        "description": "Blockchain: solana, base, ethereum, bsc. Default: solana",
                        "default": "solana",
                    },
                },
                "required": ["address"],
            },
        },
    }


def format_langchain_tool(t: dict[str, Any]) -> dict[str, Any]:
    """Format a single tool for LangChain."""
    return {
        "name": t["id"],
        "description": _tool_desc(t),
        "args_schema": {
            "address": {
                "type": "string",
                "description": f"Token or wallet address to analyze with {t['id']}",
            },
            "chain": {
                "type": "string",
                "description": "Blockchain: solana, base, ethereum, bsc. Default: solana",
                "default": "solana",
            },
        },
        "required": ["address"],
        "endpoint": f"/api/v1/x402-tools/{t['id']}",
        "method": "POST",
    }


def format_anthropic_tool(t: dict[str, Any]) -> dict[str, Any]:
    """Format a single tool for Anthropic Claude API."""
    return {
        "name": t["id"],
        "description": _tool_desc(t),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": f"Token or wallet address to analyze with {t['id']}",
                },
                "chain": {
                    "type": "string",
                    "description": "Blockchain: solana, base, ethereum, bsc. Default: solana",
                    "default": "solana",
                },
            },
            "required": ["address"],
        },
    }


def format_gemini_tool(t: dict[str, Any]) -> dict[str, Any]:
    """Format a single tool for Google Gemini function calling."""
    return {
        "name": t["id"],
        "description": _tool_desc(t),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "address": {
                    "type": "STRING",
                    "description": f"Token or wallet address to analyze with {t['id']}",
                },
                "chain": {
                    "type": "STRING",
                    "description": "Blockchain: solana, base, ethereum, bsc. Default: solana",
                },
            },
            "required": ["address"],
        },
    }


def build_framework_base(tools: list[Any], format_key: str, **extra: Any) -> dict[str, Any]:
    """Build the shared response envelope for framework-format endpoints."""
    return {
        "service": "Rug Munch Intelligence",
        "tagline": "We build tools to keep the crypto space safer",
        "total_tools": len(tools),
        "followers_x": "67,000+",
        "telegram_users": "7,000+",
        "networks": list(NETWORKS),
        "protocol": "x402",
        format_key: tools,
        **extra,
    }


# ── Rate Limiting ──────────────────────────────────────────────


async def check_rate_limit(request: Request) -> bool:
    """Simple IP-based rate limiter: 60 req/min per IP, 300 req/5min per IP.
    Uses the same Redis as x402 enforcement. Fail-open if Redis is down."""
    try:
        from app.core.redis import get_redis

        r = get_redis()
        if not r:
            return True
        cf_ip = request.headers.get("CF-Connecting-IP", "") or (request.client.host if request.client else "0")
        ip_key = _hl.sha256(cf_ip.encode()).hexdigest()[:16]
        key_min = f"rl:{ip_key}:min"
        count = r.incr(key_min)
        if count == 1:
            r.expire(key_min, 60)
        if count > 60:
            return False
        key_5m = f"rl:{ip_key}:5min"
        count5 = r.incr(key_5m)
        if count5 == 1:
            r.expire(key_5m, 300)
        return not count5 > 300
    except (ConnectionError, TimeoutError, OSError, ImportError, AttributeError):
        # Fail open — allow request if Redis is down OR the redis accessor
        # cannot be resolved. A missing symbol must never 500 a paid tool.
        return True


# ── Payment Recording ──────────────────────────────────────────


async def record_payment(tool_id: str, price_usd: str, payer: str) -> None:
    """Surface for super-tool endpoints (comprehensive audit, etc.).
    Reuses the P3A stub via the shared module."""
    from app.domains.billing.x402.shared import record_x402_payment

    await record_x402_payment(tool_id, price_usd, payer)


async def verify_onchain_direct(
    tx_hash: str,
    chain: str,
    token_info: dict[str, Any],
    expected_pay_to: str,
) -> bool:
    """Direct on-chain verification fallback for human payments."""
    async with aiohttp.ClientSession() as session:
        if chain == "solana":
            async with session.post(
                "https://api.mainnet-beta.solana.com",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        tx_hash,
                        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                    ],
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                tx = data.get("result", {})
                if not tx:
                    return False
                meta = tx.get("meta", {})
                return any(bal.get("owner") == expected_pay_to for bal in meta.get("postTokenBalances", []))

        elif chain in ("tron", "bitcoin", "sepa"):
            return True

        else:
            explorers = {
                "base": "https://api.basescan.org/api",
                "ethereum": "https://api.etherscan.io/api",
                "bsc": "https://api.bscscan.com/api",
                "polygon": "https://api.polygonscan.com/api",
                "arbitrum": "https://api.arbiscan.io/api",
            }
            explorer_url = explorers.get(chain, explorers["ethereum"])
            api_key = os.getenv("ETHERSCAN_API_KEY", "")

            async with session.get(
                f"{explorer_url}?module=transaction&action=gettxreceiptstatus&txhash={tx_hash}&apikey={api_key}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                result = data.get("result", {})
                if isinstance(result, dict):
                    return result.get("status") == "1"
                return str(data.get("status")) == "1"


# ── Super-Tool Parallel Call Helper ────────────────────────────


async def run_super_tool_batch(
    endpoints: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Run multiple tool sub-calls in parallel for super-tool composition.
    Returns dict of {name: response_data_or_None}."""

    async def _call(name: str, url: str) -> tuple[str, Any | None]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(url, json=payload)
                return name, r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, TimeoutError, json.JSONDecodeError):
            return name, None

    tasks = [_call(name, url) for name, url in endpoints.items()]
    results = await asyncio.gather(*tasks)
    return dict(results)


# ── Bundle Execution Helper ─────────────────────────────────────


async def execute_bundle_tasks(
    tasks: dict[str, str],
    body: dict[str, Any],
) -> dict[str, Any]:
    """Execute multiple POST calls in parallel for bundle endpoints.
    Returns dict of {name: response_json_or_error_dict}."""
    results: dict[str, Any] = {}
    async with aiohttp.ClientSession() as session:
        coros: dict[str, Any] = {}
        for name, url in tasks.items():
            coros[name] = session.post(
                url,
                json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            )
        for name, coro in coros.items():
            try:
                resp = await coro
                results[name] = await resp.json() if resp.status == 200 else {"status": resp.status}
            except (
                TimeoutError,
                aiohttp.ClientError,
                ConnectionError,
                OSError,
                httpx.HTTPError,
                requests.RequestException,
                json.JSONDecodeError,
                KeyError,
                TypeError,
            ) as e:
                results[name] = {"error": str(e)}
    return results


# ── Tool Alias Resolution ──────────────────────────────────────


def resolve_tool_alias(tool_id: str) -> tuple[str | None, str | None]:
    """Resolve a tool ID to its alias target and optional injected chain.
    Returns (target, injected_chain). Both None means no alias found."""
    from app.domains.billing.x402.shared import TOOL_ALIASES

    target = TOOL_ALIASES.get(tool_id)
    injected_chain = None

    if target is None and "_" in tool_id:
        try:
            from app.routers.x402_enforcement import TOOL_PRICES

            pricing = TOOL_PRICES.get(tool_id, {})
            base_tool = pricing.get("base_tool")
            if base_tool:
                target = base_tool
                injected_chain = pricing.get("chain")
        except (ImportError, KeyError) as e:
            logger.debug(f"resolve_tool_alias failed: {e}")

        if target is None:
            last_underscore = tool_id.rfind("_")
            if last_underscore > 0:
                suffix = tool_id[last_underscore + 1 :]
                prefix = tool_id[:last_underscore]
                if suffix in CHAIN_SUFFIXES:
                    target = TOOL_ALIASES.get(prefix, prefix)
                    injected_chain = suffix

    return target, injected_chain


# ── Wallet Intelligence Enrichment ─────────────────────────────


async def enrich_response(
    tool_id: str,
    result: dict[str, Any],
    body: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Apply wallet intelligence enrichment if not opted out."""
    try:
        opt_out = request.query_params.get("enrich", "").lower() == "false"
        from app.routers.x402_enrichment import enrich_tool_response

        return await asyncio.to_thread(enrich_tool_response, tool_id, result, request_params=body, opt_out=opt_out)
    except (ImportError, Exception):
        return result
