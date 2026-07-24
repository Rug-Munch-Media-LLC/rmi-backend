"""Tool catalog — /mcp/tools, /tools, /mcp/capabilities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from app.core.logging import get_logger

from .config import (
    SERVER_NAME,
    SERVER_VERSION,
    _desc,
    _get_facilitator_count,
    _get_tools,
    _input_schema,
)

logger = get_logger("rmi_mcp_v3")
router = APIRouter(tags=["mcp_catalog"])

def _build_tools_list() -> tuple[Any, Any, Any, Any]:
    """Build the full MCP-compatible tools list."""
    data = _get_tools()
    prices = data["prices"]
    chains_data = data["chains"]
    fac_count = _get_facilitator_count()

    tools = {}
    cats = {}
    from app.routers.x402_databus_tools import X402_TOOL_PRICING as DATABUS_TOOLS

    databus_ids = set(DATABUS_TOOLS.keys())
    from app.routers.x402_tools import TOOL_ALIASES

    for tool_id, pricing in sorted(prices.items()):
        cat = pricing.get("category", "analysis")
        cats[cat] = cats.get(cat, 0) + 1
        real_tool = TOOL_ALIASES.get(tool_id, tool_id)
        endpoint = (
            f"/api/v1/x402-databus/{tool_id}"
            if tool_id in databus_ids
            else f"/api/v1/x402-tools/{real_tool}"
        )
        tools[tool_id] = {
            "name": tool_id,
            "description": _desc(tool_id, pricing),
            "category": cat,
            "inputSchema": _input_schema(tool_id),
            "price_usd": float(pricing.get("price_usd", 0.01)),
            "trial_free": int(pricing.get("trial_free", 1)),
            "chains": sorted(chains_data.keys()),
            "endpoint": endpoint,
            "method": pricing.get("method", "POST")
            if isinstance(pricing.get("method"), str)
            else "POST",
            "databus": tool_id in databus_ids,
        }

    for tool_id, pricing in sorted(DATABUS_TOOLS.items()):
        if tool_id not in tools:
            cat = pricing.get("category", "data")
            cats[cat] = cats.get(cat, 0) + 1
            tools[tool_id] = {
                "name": tool_id,
                "description": _desc(tool_id, pricing)
                if pricing.get("description")
                else f"{tool_id.replace('_', ' ').title()} -- DataBus-powered crypto intelligence.",
                "category": cat,
                "inputSchema": _input_schema(tool_id),
                "price_usd": float(pricing.get("price_usd", 0.05)),
                "trial_free": int(pricing.get("trial_free", 1)),
                "chains": sorted(chains_data.keys()),
                "endpoint": f"/api/v1/x402-databus/{tool_id}",
                "method": "POST",
                "databus": True,
            }

    return tools, cats, chains_data, fac_count

@router.get("/mcp/tools")
async def mcp_tools_list(request: Request) -> dict[str, str]:
    """Every tool in the RMI platform -- full catalog."""
    tools, cats, chains_data, fac_count = _build_tools_list()

    trial_info = None
    try:
        from app.routers.x402_enforcement import check_trial, get_client_id

        cid = get_client_id(request)
        remaining = {}
        for tid in _get_tools()["prices"]:
            can, rem = check_trial(tid, cid)
            if rem > 0 or can:
                remaining[tid] = {"can_trial": can, "remaining": rem}
        trial_info = {
            "client_id": cid[:20] + "...",
            "tools_with_trials": len(remaining),
            "trials": remaining,
        }
    except (KeyError, ValueError, TypeError) as e:
        logger.debug(f"Trial info build failed: {e}")

    return {
        "server": f"{SERVER_NAME} MCP v{SERVER_VERSION}",
        "homepage": "https://rugmunch.io",
        "github": "https://github.com/cryptorugmuncher/rug-munch-intelligence",
        "directories": {
            "smithery": "https://smithery.ai/server/@cryptorugmuncher/rug-munch-intelligence",
            "glama": "https://glama.ai/mcp/servers/@cryptorugmuncher/rug-munch-intelligence",
        },
        "total_tools": len(tools),
        "categories": dict(sorted(cats.items())),
        "chains": sorted(chains_data.keys()),
        "chain_count": len(chains_data),
        "facilitators": fac_count,
        "pricing": "$0.01-$0.40/call. Most tools $0.05. Bundles save 23-33%.",
        "free_trials": "1-5 calls per tool. Fingerprint-gated.",
        "payment": {
            "protocol": "x402",
            "discovery": "/.well-known/x402",
            "tokens": ["USDC", "USDT", "BTC", "EUR"],
            "chains": sorted(chains_data.keys()),
        },
        "refund": "Full refund if tool returns no data. Within 48h.",
        "tools": tools,
        "trial_status": trial_info,
        "updated_at": datetime.now(UTC).isoformat(),
    }

@router.get("/tools")
async def mcp_tools_mcp_router_format(request: Request) -> dict:
    """Serve tools in mcp-router compatible format."""
    tools_data, _cats, _chains_data, _fac_count = _build_tools_list()
    services: dict[str, list] = {}
    for tool_id, tool_def in tools_data.items():
        svc = tool_def.get("category", "general")
        if svc not in services:
            services[svc] = []
        services[svc].append(
            {
                "name": tool_id,
                "description": tool_def.get("description", tool_id),
                "parameters": tool_def.get("inputSchema", {}),
                "tier": "free" if tool_def.get("trial_free", 0) > 0 else "paid",
                "category": svc,
                "chains": tool_def.get("chains", []),
                "price_usd": tool_def.get("price_usd", 0.01),
                "endpoint": tool_def.get("endpoint", f"/api/v1/x402-tools/{tool_id}"),
            }
        )
    return {"tools": services}

@router.get("/mcp/capabilities")
async def mcp_capabilities() -> dict[str, str]:
    data = _get_tools()
    fac_count = _get_facilitator_count()
    return {
        "server": f"{SERVER_NAME} MCP v{SERVER_VERSION}",
        "homepage": "https://rugmunch.io",
        "github": "https://github.com/cryptorugmuncher/rug-munch-intelligence",
        "capabilities": {"tools": True, "resources": False, "prompts": False, "streaming": False},
        "protocols": ["x402"],
        "payment": {
            "required": False,
            "trial_available": True,
            "trial_calls": "1-5 per tool",
            "paid": f"$0.01-$0.40 via x402 on {len(data['chains'])} chains",
        },
        "facilitators": fac_count,
        "updated_at": datetime.now(UTC).isoformat(),
    }
