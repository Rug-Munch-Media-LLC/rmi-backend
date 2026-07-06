"""
MCP Router - Receives tool execution requests from Cloudflare X402 Workers.
Exposes /mcp/tools (catalog) and /mcp/execute (tool execution).
This is what the worker calls when x402 payment is verified.
"""

import logging
import os
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp-router"])

# ═══════════════════════════════════════════════════════════
# TOOL CATALOG - What the worker fetches on startup
# ═══════════════════════════════════════════════════════════
TOOLS = {
    # Security
    "honeypot_check": {
        "name": "Honeypot Detector",
        "endpoint": "/api/v1/contract/audit",
        "method": "POST",
        "category": "security",
        "price": "$0.05",
    },
    "rug_pull_predictor": {
        "name": "Rug Pull Predictor",
        "endpoint": "/api/v1/contract/audit",
        "method": "POST",
        "category": "security",
        "price": "$0.10",
    },
    "rugshield": {
        "name": "Rug Shield",
        "endpoint": "/api/v1/security/scan",
        "method": "POST",
        "category": "security",
        "price": "$0.02",
    },
    "audit": {
        "name": "Smart Contract Audit",
        "endpoint": "/api/v1/contract/audit",
        "method": "POST",
        "category": "security",
        "price": "$0.05",
    },
    "mev_protection": {
        "name": "MEV Protection Check",
        "endpoint": "/api/v1/tools/cast/contract-info",
        "method": "GET",
        "category": "security",
        "price": "$0.08",
    },
    "bridge_security": {
        "name": "Bridge Security Monitor",
        "endpoint": "/api/v1/defillama/chains",
        "method": "GET",
        "category": "security",
        "price": "$0.08",
    },
    "profile_flip": {
        "name": "Profile Flip Detector",
        "endpoint": "/api/v1/token/scan",
        "method": "POST",
        "category": "security",
        "price": "$0.03",
    },
    "fresh_pair": {
        "name": "Fresh Pair Scanner",
        "endpoint": "/api/v1/tokens/new",
        "method": "GET",
        "category": "security",
        "price": "$0.03",
    },
    "clone_detect": {
        "name": "Clone Detector",
        "endpoint": "/api/v1/token/scan",
        "method": "POST",
        "category": "security",
        "price": "$0.02",
    },
    "urlcheck": {
        "name": "URL Safety Check",
        "endpoint": "/api/v1/security/scan",
        "method": "POST",
        "category": "security",
        "price": "$0.01",
    },
    # Intelligence
    "whale": {
        "name": "Whale Wallet Decoder",
        "endpoint": "/api/v1/helius/whale-profile",
        "method": "POST",
        "category": "intelligence",
        "price": "$0.15",
    },
    "whale_scan": {
        "name": "Whale Scanner",
        "endpoint": "/api/v1/helius/whale-scan",
        "method": "POST",
        "category": "intelligence",
        "price": "$0.03",
    },
    "whale_profile": {
        "name": "Whale Profile",
        "endpoint": "/api/v1/helius/whale-profile",
        "method": "POST",
        "category": "intelligence",
        "price": "$0.05",
    },
    "smartmoney": {
        "name": "Smart Money Tracker",
        "endpoint": "/api/v1/smart-money",
        "method": "GET",
        "category": "intelligence",
        "price": "$0.05",
    },
    "cluster": {
        "name": "Wallet Cluster Analysis",
        "endpoint": "/api/v1/entity/clusters",
        "method": "GET",
        "category": "intelligence",
        "price": "$0.05",
    },
    "insider": {
        "name": "Insider Trading Detection",
        "endpoint": "/api/v1/threat/reputation/0x0",
        "method": "GET",
        "category": "intelligence",
        "price": "$0.10",
    },
    "sniper_detect": {
        "name": "Sniper Detector",
        "endpoint": "/api/v1/helius/sniper-detect",
        "method": "POST",
        "category": "intelligence",
        "price": "$0.08",
    },
    "syndicate_scan": {
        "name": "Syndicate Scanner",
        "endpoint": "/api/v1/helius/syndicate/scan",
        "method": "GET",
        "category": "intelligence",
        "price": "$0.08",
    },
    "copy_trade_finder": {
        "name": "Copy Trade Finder",
        "endpoint": "/api/v1/whales/top",
        "method": "GET",
        "category": "intelligence",
        "price": "$0.10",
    },
    "liquidity_flow": {
        "name": "Liquidity Flow Tracker",
        "endpoint": "/api/v1/exchange/whales",
        "method": "GET",
        "category": "intelligence",
        "price": "$0.08",
    },
    # Market
    "pulse": {
        "name": "Market Pulse",
        "endpoint": "/api/v1/intelligence/dashboard",
        "method": "GET",
        "category": "market",
        "price": "$0.01",
    },
    "market_overview": {
        "name": "Market Overview",
        "endpoint": "/api/v1/markets/ccxt",
        "method": "GET",
        "category": "market",
        "price": "$0.05",
    },
    "chain_health": {
        "name": "Chain Health",
        "endpoint": "/api/v1/defillama/chains",
        "method": "GET",
        "category": "market",
        "price": "$0.05",
    },
    "defi_yield_scanner": {
        "name": "DeFi Yield Scanner",
        "endpoint": "/api/v1/defillama/protocols",
        "method": "GET",
        "category": "market",
        "price": "$0.08",
    },
    "gas_forecast": {
        "name": "Gas Forecaster",
        "endpoint": "/api/v1/mempool/status",
        "method": "GET",
        "category": "market",
        "price": "$0.05",
    },
    # Analysis
    "wallet": {
        "name": "Wallet Analysis",
        "endpoint": "/api/v1/wallet/multichain/{address}",
        "method": "GET",
        "category": "analysis",
        "price": "$0.05",
    },
    "forensics": {
        "name": "Token Forensics",
        "endpoint": "/api/v1/wallet/{address}/analysis",
        "method": "GET",
        "category": "analysis",
        "price": "$0.10",
    },
    "token_deep_dive": {
        "name": "Token Deep Dive",
        "endpoint": "/api/v1/birdeye/token/{address}",
        "method": "GET",
        "category": "analysis",
        "price": "$0.10",
    },
    "portfolio_tracker": {
        "name": "Portfolio Tracker",
        "endpoint": "/api/v1/wallet/pnl/{address}",
        "method": "GET",
        "category": "analysis",
        "price": "$0.10",
    },
    "token_comparison": {
        "name": "Token Comparison",
        "endpoint": "/api/v1/token/scan",
        "method": "POST",
        "category": "analysis",
        "price": "$0.08",
    },
    "nft_wash_detector": {
        "name": "NFT Wash Detector",
        "endpoint": "/api/v1/threat/reputation/0x0",
        "method": "GET",
        "category": "analysis",
        "price": "$0.10",
    },
    # Launch
    "launch": {
        "name": "Token Launch Analysis",
        "endpoint": "/api/v1/tokens/new",
        "method": "GET",
        "category": "launchpad",
        "price": "$0.03",
    },
    "launch_intel": {
        "name": "Launch Intelligence",
        "endpoint": "/api/v1/tokens/trending",
        "method": "GET",
        "category": "launchpad",
        "price": "$0.05",
    },
    "sniper_alert": {
        "name": "Sniper Alert",
        "endpoint": "/api/v1/tokens/new",
        "method": "GET",
        "category": "launchpad",
        "price": "$0.05",
    },
    "airdrop_finder": {
        "name": "Airdrop Finder",
        "endpoint": "/api/v1/wallet/pnl/{address}",
        "method": "GET",
        "category": "intelligence",
        "price": "$0.05",
    },
    # Social
    "sentiment": {
        "name": "Sentiment Analysis",
        "endpoint": "/api/v1/sentiment/market",
        "method": "GET",
        "category": "social",
        "price": "$0.03",
    },
    "social_signal": {
        "name": "Social Signal Analyzer",
        "endpoint": "/api/v1/sentiment/token/{address}",
        "method": "GET",
        "category": "social",
        "price": "$0.10",
    },
}


def _build_tools_catalog() -> dict:
    """Merge hardcoded TOOLS with dynamic TOOL_PRICES, preferring dynamic data."""
    merged = dict(TOOLS)  # Start with hardcoded
    try:
        from app.routers.x402_enforcement import TOOL_PRICES

        # Tool ID → internal endpoint mapping
        _ENDPOINT_MAP = {
            "forensic_valuation": "/api/v1/x402-tools/forensic_valuation",
            "osint_identity_hunt": "/api/v1/x402-tools/osint_identity_hunt",
            "investigation_report": "/api/v1/x402-tools/investigation_report",
            "forensic_pack": "/api/v1/x402-tools/forensic_pack",
            "catalog": "/api/v1/x402-tools/catalog",
            "smart_money_alpha": "/api/v1/x402-tools/smart_money_alpha",
            "meme_vibe_score": "/api/v1/x402-tools/meme_vibe_score",
            "mcp-proxy": "/api/v1/x402-tools/mcp-proxy",
            "human-execute": "/api/v1/x402-tools/human-execute",
        }
        for tool_id, pricing in TOOL_PRICES.items():
            if tool_id not in merged:
                endpoint = _ENDPOINT_MAP.get(tool_id, f"/api/v1/x402-tools/{tool_id}")
                merged[tool_id] = {
                    "name": pricing.get("description", tool_id.replace("_", " ").title()),
                    "endpoint": endpoint,
                    "method": "POST",
                    "category": pricing.get("category", "analysis"),
                    "price": f"${pricing.get('price_usd', 0.01):.2f}",
                }
            else:
                # Update existing entries with latest pricing/category from TOOL_PRICES
                if tool_id in TOOL_PRICES:
                    merged[tool_id]["category"] = TOOL_PRICES[tool_id].get(
                        "category", merged[tool_id].get("category", "analysis")
                    )
                    merged[tool_id]["price"] = f"${TOOL_PRICES[tool_id].get('price_usd', 0.01):.2f}"
    except Exception as e:
        logger.warning(f"mcp_router: could not merge TOOL_PRICES: {e}")
    return merged


@router.get("/tools")
async def mcp_tools():
    """Return tool catalog for Cloudflare Worker to cache."""
    catalog = _build_tools_catalog()
    return {
        "tools": catalog,
        "total": len(catalog),
        "categories": list({t["category"] for t in catalog.values()}),
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.post("/execute/{tool_id}")
async def mcp_execute(tool_id: str, request: Request):
    """Execute a tool - called by Cloudflare Worker after x402 payment verified."""
    tool = TOOLS.get(tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool {tool_id} not found")

    try:
        body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    except Exception:
        body = {}

    # Forward to the actual backend endpoint
    endpoint = tool["endpoint"]
    method = tool["method"]

    # Replace path parameters from body
    for key in ["address", "token", "chain"]:
        if f"{{{key}}}" in endpoint and key in body:
            endpoint = endpoint.replace(f"{{{key}}}", body[key])

    # Internal calls bypass auth - add internal API key header
    internal_headers = {}
    auth_token = os.getenv("RMI_AUTH_TOKEN", "")
    if auth_token:
        internal_headers["X-API-Key"] = auth_token

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            if method == "GET":
                # Build query params from body
                params = {k: v for k, v in body.items() if k not in ["address", "token"] and f"{{{k}}}" not in endpoint}
                r = await c.get(f"http://127.0.0.1:8000{endpoint}", params=params, headers=internal_headers)
            else:
                r = await c.post(f"http://127.0.0.1:8000{endpoint}", json=body, headers=internal_headers)

            if r.status_code == 200:
                return {
                    "tool": tool_id,
                    "status": "success",
                    "data": r.json(),
                    "executed_at": datetime.now(UTC).isoformat(),
                }
            return {
                "tool": tool_id,
                "status": "error",
                "error": f"Backend returned {r.status_code}",
                "executed_at": datetime.now(UTC).isoformat(),
            }
    except Exception as e:
        return {"tool": tool_id, "status": "error", "error": str(e)}


@router.get("/health")
async def mcp_health():
    catalog = _build_tools_catalog()
    return {"status": "healthy", "tools": len(catalog), "timestamp": datetime.now(UTC).isoformat()}
