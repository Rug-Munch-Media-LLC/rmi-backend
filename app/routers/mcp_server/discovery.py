"""MCP Discovery endpoints — /.well-known/mcp, /llms.txt, etc."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from app.core.logging import get_logger

from .config import MCP_PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION, _desc, _get_facilitator_count, _get_tools

logger = get_logger("rmi_mcp_v3")
router = APIRouter(tags=["mcp_discovery"])

def _build_discovery() -> dict:
    """Build the MCP server discovery document."""
    data = _get_tools()
    tools = data["prices"]
    chains = data["chains"]
    fac_count = _get_facilitator_count()
    cats = sorted({p.get("category", "analysis") for p in tools.values()})

    return {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "description": f"{len(tools)} crypto intelligence tools -- real-time scam detection, wallet forensics, whale tracking, contract auditing, market analysis. {len(chains)} blockchains, micropayments via x402.",
        "protocol": "mcp",
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "vendor": {
            "name": "Rug Munch Intelligence",
            "url": "https://rugmunch.io",
            "github": "https://github.com/Rug-Munch-Media-LLC",
        },
        "homepage": "https://rugmunch.io",
        "documentation": "https://rugmunch.io/mcp-docs",
        "repository": "https://github.com/Rug-Munch-Media-LLC/rug-munch-intelligence-mcp",
        "endpoint": "https://mcp.rugmunch.io/mcp",
        "icon": "https://rugmunch.io/logo.png",
        "transports": ["http"],
        "authentication": {
            "type": "x402",
            "description": "Pay-per-use micropayments. 1-5 free trials per tool.",
            "discovery_url": "https://mcp.rugmunch.io/.well-known/x402",
            "wallet_providers": {
                "evm": {
                    "description": "Any EIP-7702 compatible wallet",
                    "chains": ["base", "ethereum", "bsc", "polygon", "arbitrum", "optimism", "avalanche", "fantom", "gnosis"],
                    "facilitators": ["coinbase_cdp", "eip7702", "cloudflare_x402", "payai"],
                },
                "solana": {
                    "description": "Phantom, Solflare, Backpack",
                    "chains": ["solana"],
                    "facilitators": ["coinbase_cdp", "payai"],
                },
                "tron": {
                    "description": "TronLink, TokenPocket",
                    "chains": ["tron"],
                    "facilitators": ["tron_selfverify"],
                },
                "bitcoin": {
                    "description": "Any Bitcoin wallet",
                    "chains": ["bitcoin"],
                    "facilitators": ["bitcoin_selfverify"],
                },
                "fiat": {
                    "description": "EUR/SEPA bank transfer via Asterpay",
                    "chains": ["sepa"],
                    "facilitators": ["asterpay"],
                },
            },
        },
        "capabilities": {"tools": True, "resources": False, "prompts": False},
        "stats": {
            "total_tools": len(tools),
            "categories": cats,
            "chains": sorted(chains.keys()),
            "chain_count": len(chains),
            "facilitators": fac_count,
            "free_trials": "1-5 calls per tool",
            "pricing": "$0.01 - $0.40 per call",
            "updated_at": datetime.now(UTC).isoformat(),
        },
        "categories": cats,
        "blockchains": sorted(chains.keys()),
        "facilitator_count": fac_count,
        "tools_count": len(tools),
        "tools": [
            {
                "name": tid,
                "description": _desc(tid, t)[:200],
                "category": t.get("category", "analysis"),
                "price_usd": t.get("price_usd", 0.01),
                "trial_free": t.get("trial_free", 1),
            }
            for tid, t in sorted(tools.items())
        ],
    }

@router.get("/.well-known/mcp")
@router.get("/.well-known/mcp.json")
async def mcp_discovery() -> Any:
    return _build_discovery()

@router.get("/.well-known/ai-plugin.json")
async def ai_plugin_manifest() -> dict[str, str]:
    data = _get_tools()
    count = len(data["prices"])
    return {
        "schema_version": "v1",
        "name_for_human": SERVER_NAME,
        "name_for_model": "rug_munch_intelligence",
        "description_for_human": f"{SERVER_NAME} -- crypto intelligence: scam detection, wallet forensics, whale tracking. {count} tools, {len(data['chains'])} chains.",
        "description_for_model": f"Use for crypto security: check tokens for scams, honeypots, rug pulls. {count} tools, {len(data['chains'])} chains.",
        "auth": {"type": "none"},
        "api": {"type": "openapi", "url": "https://mcp.rugmunch.io/openapi.json"},
        "logo_url": "https://rugmunch.io/logo.png",
        "contact_email": "mcp@rugmunch.io",
        "legal_info_url": "https://rugmunch.io/terms",
    }

@router.get("/llms.txt")
async def llms_txt() -> Any:
    data = _get_tools()
    prices = data["prices"]
    chains = data["chains"]
    fac_count = _get_facilitator_count()
    cats = {}
    for _tool_id, pricing in prices.items():
        cat = pricing.get("category", "analysis")
        cats[cat] = cats.get(cat, 0) + 1
    cat_lines = [f"- {c.title()} ({n})" for c, n in sorted(cats.items())]
    chain_list = ", ".join(sorted(chains.keys()))

    return Response(
        content=f"""# Rug Munch Intelligence -- MCP Server
> {len(prices)} crypto intelligence tools. {len(chains)} chains. Free trials + x402 micropayments.

## Quick Start
- MCP Endpoint: https://mcp.rugmunch.io/mcp
- Discovery: https://mcp.rugmunch.io/.well-known/mcp
- Docs: https://rugmunch.io/mcp-docs

## Tool Categories
{chr(10).join(cat_lines)}

## Payment Chains
{chain_list}
""",
        media_type="text/plain; charset=utf-8",
    )

@router.options("/mcp")
async def mcp_options() -> Any:
    """CORS preflight for MCP endpoint."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Pay, X-Device-Id, X-Wallet-Address",
            "Access-Control-Max-Age": "86400",
        },
    )
