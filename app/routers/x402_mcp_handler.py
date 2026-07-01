"""x402 MCP handler — exposes x402 payment as discoverable MCP tools.

Mounts at /mcp/x402 for MCP protocol compatibility.
Also serves /.well-known/x402 for standard x402 discovery.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.mcp.x402_mcp_server import TOOLS, handle_mcp_call

router = APIRouter(tags=["x402-MCP"])

# ── Standard MCP endpoint ────────────────────────────────────────


@router.post("/mcp/x402")
async def mcp_x402_handler(request: Request):
    """MCP protocol handler for x402 payment tools.

    Accepts MCP-standard requests: {"method": "tools/list"} or
    {"method": "tools/call", "params": {"name": "...", "arguments": {...}}}
    """
    body = await request.json()
    method = body.get("method", "")

    if method == "tools/list":
        return {
            "result": {
                "tools": TOOLS,
                "server": {
                    "name": "x402-payment-gateway",
                    "version": "1.0.0",
                    "description": "Pay-per-call AI tool access via x402 protocol. 274+ tools across crypto intelligence, security, and analysis.",
                },
            }
        }

    if method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result = await handle_mcp_call(tool_name, arguments)
            return {"result": result}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    raise HTTPException(status_code=400, detail=f"Unknown method: {method}")


# ── x402 Discovery endpoint ──────────────────────────────────────


@router.get("/.well-known/x402")
async def well_known_x402():
    """x402 service discovery document (RFC-like).

    Standard endpoint that x402-compatible clients check to discover
    payment capabilities, supported chains, pricing, and facilitator info.
    """
    from app.routers.x402_enforcement import _ensure_tool_prices, TOOL_PRICES, CHAIN_USDC
    _ensure_tool_prices()

    paid = sum(1 for v in TOOL_PRICES.values() if v.get("price_usd", 0) > 0)
    min_price = min(v["price_usd"] for v in TOOL_PRICES.values() if v.get("price_usd", 0) > 0)
    max_price = max(v["price_usd"] for v in TOOL_PRICES.values() if v.get("price_usd", 0) > 0)

    return {
        "x402_version": "2.0",
        "service": "Rug Munch Intelligence x402 Gateway",
        "operator": "Rug Munch Media LLC",
        "discovery_url": "https://mcp.rugmunch.io/.well-known/x402",
        "mcp_url": "https://mcp.rugmunch.io/mcp/x402",
        "docs_url": "https://docs.rugmunch.io/x402",
        "payment_address": os.getenv("X402_PAYMENT_ADDRESS", ""),
        "supported_chains": {
            chain: {
                "network": cfg.get("network", ""),
                "usdc_address": cfg.get("usdc", ""),
            }
            for chain, cfg in CHAIN_USDC.items()
        },
        "pricing": {
            "total_tools": len(TOOL_PRICES),
            "paid_tools": paid,
            "price_range_usd": {"min": min_price, "max": max_price},
            "free_trials": True,
        },
        "facilitators": [
            "coinbase_cdp",
            "payai",
            "cloudflare_x402",
            "x402_rs",
            "primev",
            "asterpay",
            "eip7702",
        ],
        "status": "active",
    }
