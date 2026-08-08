"""Main x402 integration — router, framework discovery, MCP proxy,
bundle endpoints, and catch-all dispatchers."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import aiohttp
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.domains.billing.x402.shared import (
    td,
)

from .utils import (
    build_framework_base,
    build_tools_from_catalog,
    check_rate_limit,
    enrich_response,
    execute_bundle_tasks,
    format_anthropic_tool,
    format_gemini_tool,
    format_langchain_tool,
    format_openai_tool,
    resolve_tool_alias,
)

sub_router = APIRouter(tags=["x402-integration"])

# ═══════════════════════════════════════════════════════════════
# Framework Discovery & Format Endpoints
# ═══════════════════════════════════════════════════════════════


@sub_router.get("/discovery")
async def tools_discovery() -> Any:
    """Human-friendly tool discovery - organized by category with SEO descriptions."""
    from app.tool_catalog import get_human_catalog

    return get_human_catalog()


@sub_router.get("/catalog")
async def tools_catalog_bot() -> Any:
    """Bot-optimized tool catalog - flat list with IDs, pricing, chain support."""
    from app.tool_catalog import get_bot_catalog

    return get_bot_catalog()


@sub_router.get("/openai-tools")
async def openai_tools() -> dict[str, Any]:
    """Returns ALL 201+ tool definitions in OpenAI function calling format."""
    raw_tools = await build_tools_from_catalog()
    tools = [format_openai_tool(t) for t in raw_tools]
    return build_framework_base(tools, "tools", payment_required=True)


@sub_router.get("/langchain-tools")
async def langchain_tools() -> dict[str, Any]:
    """Returns ALL 201+ tool definitions in LangChain format."""
    raw_tools = await build_tools_from_catalog()
    tools = [format_langchain_tool(t) for t in raw_tools]
    return build_framework_base(
        tools,
        "tools",
        format="langchain",
        usage="pip install langchain && use with create_react_agent or LangGraph",
    )


@sub_router.get("/anthropic-tools")
async def anthropic_tools() -> dict[str, Any]:
    """Returns ALL 201+ tool definitions in Anthropic Claude API format."""
    raw_tools = await build_tools_from_catalog()
    tools = [format_anthropic_tool(t) for t in raw_tools]
    return build_framework_base(
        tools,
        "tools",
        format="anthropic_claude_api",
        usage="pip install anthropic && use with client.messages.create(tools=...)",
    )


@sub_router.get("/gemini-tools")
async def gemini_tools() -> dict[str, Any]:
    """Returns ALL 201+ tool definitions in Google Gemini function calling format."""
    raw_tools = await build_tools_from_catalog()
    tools = [format_gemini_tool(t) for t in raw_tools]
    return build_framework_base(
        tools,
        "function_declarations",
        format="google_gemini",
        usage="pip install google-genai && use with model.generate_content(tools=...)",
    )


@sub_router.get("/frameworks")
async def framework_discovery() -> dict[str, Any]:
    """Returns all available framework integrations with endpoints."""
    base = "https://mcp.rugmunch.io/api/v1/x402-tools"
    return {
        "service": "Rug Munch Intelligence",
        "tagline": "We build tools to keep the crypto space safer",
        "followers_x": "67,000+",
        "telegram_users": "7,000+",
        "networks": ["base", "solana"],
        "protocol": "x402",
        "frameworks": {
            "openai": {
                "endpoint": f"{base}/openai-tools",
                "format": "OpenAI function calling",
                "usage": "client.chat.completions.create(tools=...)",
                "models": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
                "free": True,
            },
            "anthropic": {
                "endpoint": f"{base}/anthropic-tools",
                "format": "Claude API tool use",
                "usage": "client.messages.create(tools=...)",
                "models": ["claude-sonnet-4", "claude-opus-4", "claude-haiku"],
                "free": True,
            },
            "gemini": {
                "endpoint": f"{base}/gemini-tools",
                "format": "Google Gemini function calling",
                "usage": "model.generate_content(tools=...)",
                "models": ["gemini-2.0-flash", "gemini-2.5-pro"],
                "free": True,
            },
            "langchain": {
                "endpoint": f"{base}/langchain-tools",
                "format": "LangChain tool definitions",
                "usage": "create_react_agent or LangGraph",
                "ecosystem": "langchain, langgraph, crewai, autogen",
                "free": True,
            },
            "mcp": {
                "endpoint": "python -m app.mcp.x402_mcp_server",
                "format": "Model Context Protocol (stdio)",
                "usage": "Claude Desktop, Claude Code, any MCP client",
                "free": True,
            },
            "rest_api": {
                "endpoint": f"{base}/{{tool_name}}",
                "format": "HTTP POST/GET with JSON",
                "usage": "Any language, any framework",
                "payment": "x402 (USDC on Base/Solana)",
            },
        },
        "x402_gateways": {
            "base": "https://x402.rugmunch.io/tools/{tool}",
            "solana": "https://x402-sol.rugmunch.io/tools/{tool}",
            "payment": "USDC via x402 protocol",
        },
    }


# ═══════════════════════════════════════════════════════════════
# MCP Proxy — external MCP tool execution from workers
# ═══════════════════════════════════════════════════════════════

MCP_SERVICE_ROUTES: dict[str, str] = {
    "dexscreener": "https://api.dexscreener.com",
    "jupiter": "https://quote-api.jup.ag/v6",
    "pumpfun": "https://frontend-api.pump.fun",
    "raydium": "https://api.raydium.io/v2",
    "defillama": "https://api.llama.fi",
    "dexpaprika": "https://api.dexpaprika.com",
    "coincap": "https://api.coincap.io/v2",
    "coinmarketcap": "https://pro-api.coinmarketcap.com/v1",
    "cryptopanic": "https://cryptopanic.com/api/v1",
    "cryptocompare": "https://min-api.cryptocompare.com/data",
    "blockchair": "https://api.blockchair.com",
    "blockchain": "https://blockchain.info",
    "mempool": "https://mempool.space/api",
    "solana": "https://api.mainnet-beta.solana.com",
    "helius": "https://mainnet.helius-rpc.com/v0",
    "birdeye": "https://public-api.birdeye.com",
    "coingecko": "https://api.coingecko.com/api/v3",
    "cryptoiz": "https://api.cryptoiz.com",
    "blockrun": "https://api.blockrun.ai",
    "agentfi": "https://api.agentfi.xyz",
    "moralis": "https://deep-index.moralis.io/api/v2.2",
    "gmgn": "https://gmgn.ai/api",
    "nansen": "https://api.nansen.ai",
    "arkham": "https://api.arkhamintelligence.com",
    "dune": "https://api.dune.com/api/v1",
    "solscan": "https://public-api.solscan.io",
    "quicknode": "https://api.quicknode.com",
}


@sub_router.post("/mcp-proxy")
async def mcp_proxy(req: Any) -> dict[str, Any]:
    """Proxy MCP tool calls from Cloudflare Workers to external APIs."""
    from app.domains.billing.x402.shared import MCPProxyRequest

    req = MCPProxyRequest.model_validate(req) if isinstance(req, dict) else req
    svc = req.service.lower()
    tool = req.tool
    args = req.arguments or {}

    base_url = MCP_SERVICE_ROUTES.get(svc)
    if not base_url:
        return {
            "error": f"Unsupported service: {svc}",
            "available": list(MCP_SERVICE_ROUTES.keys()),
        }

    endpoint_map: dict[str, str] = {
        "getLatestTokenProfiles": "/token-profiles/latest/v1",
        "getLatestBoostedTokens": "/token-boosted/latest/v1",
        "getQuote": "/quote",
        "getPrice": "/simple/price",
        "getTokens": "/tokens",
    }
    # Dynamic endpoint construction
    if "getPairs" in tool:
        addresses = args.get("pairAddresses", args.get("tokenAddresses", ""))
        endpoint_map["getPairs"] = f"/latest/dex/pairs/solana/{addresses}"

    # DeFiLlama
    if "getTVL" in tool:
        endpoint_map["getTVL"] = f"/tvl/{args.get('protocol', '')}"
    if "getProtocols" in tool:
        endpoint_map["getProtocols"] = "/protocols"

    endpoint = endpoint_map.get(tool, f"/{tool}")
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{base_url}{endpoint}"
            headers = {"Accept": "application/json"}
            if svc == "coingecko":
                headers["x-cg-pro-api-key"] = os.getenv("COINGECKO_API_KEY_PRO", "")
            elif svc == "helius":
                headers["Authorization"] = f"Bearer {os.getenv('HELIUS_API_KEY', '')}"
            elif svc == "moralis":
                headers["X-API-Key"] = os.getenv("MORALIS_API_KEY", "")
            elif svc == "birdeye":
                headers["X-API-KEY"] = os.getenv("BIRDEYE_API_KEY", "")

            async with session.get(
                url,
                params=args,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                return {
                    "service": svc,
                    "tool": tool,
                    "status": "success" if resp.status < 400 else "error",
                    "data": data,
                }
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        return {
            "service": svc,
            "tool": tool,
            "status": "error",
            "error": str(e),
            "note": "Direct external API calls failed — try REST endpoint for cached/fallback data",
        }


# ═══════════════════════════════════════════════════════════════
# Bundle Pricing Endpoints
# ═══════════════════════════════════════════════════════════════

import contextlib  # noqa: E402 — module docstring precedes import block

from app.domains.billing.x402.shared import (  # noqa: E402 — module docstring precedes import block
    BUNDLES as _BUNDLES,
    BundleRequest,
)


@sub_router.get("/bundles")
async def list_bundles() -> dict[str, dict[str, Any]]:
    """List all available tool bundles with pricing and savings."""
    return {
        "bundles": {
            bid: {
                "name": b["name"],
                "description": b["description"],
                "tools": b["tools"],
                "individual_total": f"${b['individual_total']:.2f}",
                "bundle_price": f"${b['bundle_price_usd']:.2f}",
                "savings": f"{int((1 - b['bundle_price_usd'] / b['individual_total']) * 100)}%",
                "trial_free": b["trial_free"],
            }
            for bid, b in _BUNDLES.items()
        }
    }


@sub_router.post("/bundles/security_pack")
async def bundle_security_pack(req: BundleRequest) -> dict[str, Any]:
    """Security Pack: rugshield + audit + urlcheck + honeypot_check at 23% discount."""
    target = req.address or req.token or req.url
    if not target:
        raise HTTPException(status_code=400, detail="Provide address, token, or url")

    tasks = {
        "rugshield": "http://localhost:8000/api/v1/x402-tools/rugshield",
        "audit": "http://localhost:8000/api/v1/x402-tools/audit",
        "urlcheck": "http://localhost:8000/api/v1/x402-tools/urlcheck",
        "honeypot_check": "http://localhost:8000/api/v1/x402-tools/honeypot_check",
    }
    body = {"address": target, "token_address": target, "url": target, "chain": req.chain}
    results = await execute_bundle_tasks(tasks, body)

    verdict = "SAFE"
    risk_scores = []
    for r in results.values():
        if isinstance(r, dict):
            rs = r.get("risk_score")
            if rs is not None:
                risk_scores.append(rs)
            if r.get("risk_level") in ("CRITICAL", "HIGH"):
                verdict = "CAUTION"

    avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0
    if avg_risk > 60:
        verdict = "HIGH_RISK"
    elif avg_risk > 40:
        verdict = "CAUTION"

    return {
        "tool": "Security Pack",
        "bundle": "security_pack",
        "target": target,
        "chain": req.chain,
        "timestamp": datetime.now(UTC).isoformat(),
        "results": results,
        "summary": {
            "verdict": verdict,
            "avg_risk_score": round(avg_risk, 1),
            "tools_checked": len(results),
        },
        "price_usd": "0.10",
        "savings": "23% vs individual calls",
        "guarantee": "Data delivered or auto-refund via x402 receipt",
    }


@sub_router.post("/bundles/intelligence_pack")
async def bundle_intelligence_pack(req: BundleRequest) -> dict[str, Any]:
    """Intelligence Pack: whale + smartmoney + cluster + insider at 29% discount."""
    wallet = req.wallet or req.address
    if not wallet:
        raise HTTPException(status_code=400, detail="Provide wallet or address")

    tasks = {
        "whale": "http://localhost:8000/api/v1/x402-tools/whale",
        "smartmoney": "http://localhost:8000/api/v1/x402-tools/smartmoney",
        "cluster": "http://localhost:8000/api/v1/x402-tools/cluster",
        "insider": "http://localhost:8000/api/v1/x402-tools/insider",
    }
    body = {"address": wallet, "wallet_address": wallet, "chain": req.chain}
    results = await execute_bundle_tasks(tasks, body)

    return {
        "tool": "Intelligence Pack",
        "bundle": "intelligence_pack",
        "wallet": wallet,
        "chain": req.chain,
        "timestamp": datetime.now(UTC).isoformat(),
        "results": results,
        "price_usd": "0.25",
        "savings": "29% vs individual calls",
        "guarantee": "Data delivered or auto-refund via x402 receipt",
    }


@sub_router.post("/bundles/all_in_one")
async def bundle_all_in_one(req: BundleRequest) -> dict[str, Any]:
    """All-in-One: comprehensive_audit + smart_money_alpha + meme_vibe_score at 30% discount."""
    target = req.address or req.token or req.wallet
    if not target:
        raise HTTPException(status_code=400, detail="Provide address, token, or wallet")

    tasks = {
        "comprehensive_audit": "http://localhost:8000/api/v1/x402-tools/comprehensive_audit",
        "smart_money_alpha": "http://localhost:8000/api/v1/x402-tools/smart_money_alpha",
        "meme_vibe_score": "http://localhost:8000/api/v1/x402-tools/meme_vibe_score",
    }
    body = {"address": target, "token": target, "wallet": target, "chain": req.chain}
    results = await execute_bundle_tasks(tasks, body)

    return {
        "tool": "All-in-One Audit",
        "bundle": "all_in_one",
        "target": target,
        "chain": req.chain,
        "timestamp": datetime.now(UTC).isoformat(),
        "results": results,
        "price_usd": "0.35",
        "savings": "30% vs individual calls",
        "guarantee": "Data delivered or auto-refund via x402 receipt",
    }


@sub_router.post("/forensic_pack")
async def bundle_forensic_pack(req: BundleRequest) -> dict[str, Any]:
    """Forensic Investigation Pack — valuation + OSINT + report at 33% discount."""
    target = req.address or req.token or req.wallet
    if not target:
        raise HTTPException(status_code=400, detail="Provide address, token, or wallet")

    tasks = {
        "forensic_valuation": "http://localhost:8000/api/v1/x402-tools/forensic_valuation",
        "osint_identity_hunt": "http://localhost:8000/api/v1/x402-tools/osint_identity_hunt",
        "investigation_report": "http://localhost:8000/api/v1/x402-tools/investigation_report",
    }
    body = {"address": target, "token": target, "wallet": target, "chain": req.chain}
    results = await execute_bundle_tasks(tasks, body)

    return {
        "tool": "Forensic Investigation Pack",
        "bundle": "forensic_pack",
        "target": target,
        "chain": req.chain,
        "timestamp": datetime.now(UTC).isoformat(),
        "results": results,
        "price_usd": "0.40",
        "savings": "33% vs individual calls",
        "guarantee": "Data delivered or auto-refund via x402 receipt",
    }


# ═══════════════════════════════════════════════════════════════
# Catch-All Tool Dispatchers
# ═══════════════════════════════════════════════════════════════


@sub_router.get("/{tool_id}")
async def tool_alias_dispatcher_get(tool_id: str, request: Request) -> Any:
    """GET catch-all for paid tools — converts query params to JSON body and dispatches."""

    query_params = dict(request.query_params)
    if not query_params:
        pricing_info = {}
        try:
            from app.routers.x402_enforcement import TOOL_PRICES

            pricing_info = TOOL_PRICES.get(tool_id, {})
        except (ImportError, KeyError):
            pass
        if pricing_info:
            return JSONResponse(
                content={
                    "tool": tool_id,
                    "description": pricing_info.get("description", ""),
                    "price_usd": pricing_info.get("price_usd", 0),
                    "trial_free": pricing_info.get("trial_free", 3),
                    "usage": "POST with JSON body or GET with query params (address, chain, etc.)",
                    "method": "GET or POST",
                }
            )
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' not found.")

    if not await check_rate_limit(request):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 60 req/min per IP.")

    target, injected_chain = resolve_tool_alias(tool_id)
    if target is None:
        target = tool_id

    body = dict(query_params)
    if injected_chain and isinstance(body, dict):
        body.setdefault("chain", injected_chain)

    try:
        result = await td.call_tool(tool_id, body)
        if result is None:
            target_url = f"http://localhost:8000/api/v1/x402-tools/{target}"
            headers = {"Content-Type": "application/json", "User-Agent": "RMI-GET-Proxy/3.1"}
            for h in (
                "X-RMI-Payment",
                "X-RMI-Trial",
                "X-RMI-Trial-Remaining",
                "X-RMI-Internal",
                "X-Device-Id",
                "X-Wallet-Address",
                "Authorization",
            ):
                val = request.headers.get(h)
                if val:
                    headers[h] = val
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(target_url, json=body, headers=headers)
                try:
                    result = resp.json()
                except (json.JSONDecodeError, ValueError):
                    result = {"data": resp.text, "status": resp.status_code}
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {e!s}") from e

    if isinstance(result, dict):
        result["alias_of"] = target
        result["tool"] = tool_id
        result["method"] = "GET"
        if injected_chain:
            result["chain"] = injected_chain

    with contextlib.suppress(ImportError, Exception):
        result = await enrich_response(tool_id, result, body, request)

    return JSONResponse(content=result, status_code=200)


@sub_router.post("/{tool_id}")
async def tool_alias_dispatcher(tool_id: str, request: Request) -> Any:
    """Catch-all dispatcher for tool aliases and per-chain variants.

    Handles three cases:
    1. Named aliases (scam_database -> urlcheck)
    2. Per-chain variants (wallet_solana -> wallet with chain=solana)
    3. Expanded tools (flash_loan_detect -> closest real handler)

    Returns 404 for truly unknown tools.
    """

    if not await check_rate_limit(request):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 60 req/min per IP.")

    target, injected_chain = resolve_tool_alias(tool_id)

    if target is None:
        body: dict[str, Any] = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request.json()
            except (json.JSONDecodeError, ValueError):
                body = {}

        if injected_chain and isinstance(body, dict):
            body.setdefault("chain", injected_chain)

        try:
            result = await td.call_tool(tool_id, body)
            if result is not None:
                if isinstance(result, dict):
                    result["tool"] = tool_id
                    result["alias_of"] = "databus"
                    if injected_chain:
                        result["chain"] = injected_chain
                with contextlib.suppress(ImportError, Exception):
                    result = await enrich_response(tool_id, result, body, request)
                return JSONResponse(content=result, status_code=200)
        except (ImportError, Exception):  # noqa: S110 — optional dependency
            pass
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{tool_id}' not found. See /mcp/tools for available tools",
        )

    target_url = f"http://localhost:8000/api/v1/x402-tools/{target}"
    try:
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    except (json.JSONDecodeError, ValueError):
        body = {}

    if injected_chain and isinstance(body, dict):
        body.setdefault("chain", injected_chain)

    headers: dict[str, str] = {}
    for h in (
        "x-pay",
        "X-Pay",
        "User-Agent",
        "Authorization",
        "x-wallet-address",
        "X-Wallet-Address",
        "x-turnstile-token",
        "X-Turnstile-Token",
        "X-Forwarded-For",
        "X-Real-IP",
        "CF-Connecting-IP",
        "X-RMI-Internal",
        "X-RMI-Payment",
        "X-RMI-Trial",
        "X-RMI-Trial-Remaining",
        "X-Device-Id",
    ):
        val = request.headers.get(h)
        if val:
            headers[h] = val
    headers["content-type"] = "application/json"
    headers.setdefault("User-Agent", "RMI-Alias-Proxy/3.1")
    headers["X-RMI-Alias"] = tool_id

    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(target_url, json=body, headers=headers)
        try:
            result = resp.json()
        except (json.JSONDecodeError, ValueError):
            result = {"data": resp.text, "status": resp.status_code}

    rh = {}
    for h in ("X-RMI-Payment", "X-RMI-Trial", "X-RMI-Trial-Remaining", "X-RMI-Refund-Flagged"):
        if resp.headers.get(h):
            rh[h] = resp.headers[h]

    if isinstance(result, dict):
        result["alias_of"] = target
        result["tool"] = tool_id
        if injected_chain:
            result["chain"] = injected_chain

    with contextlib.suppress(ImportError, Exception):
        result = await enrich_response(tool_id, result, body, request)

    return JSONResponse(content=result, status_code=resp.status_code, headers=rh)
