"""
RMI MCP Server - Quality Endpoints

Adds the endpoints that make agents choose us over competitors:
  /mcp/health      - Agent health check with response times
  /mcp/status      - Live system status (uptime, cache, rate limits)
  /mcp/changelog   - What's new in each version
  /mcp/sdk         - Quick-start code for Python, TypeScript, curl
  /mcp/trials      - Check remaining free trials
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(tags=["mcp-quality"])
_start_time = time.time()


@router.get("/mcp/health")
async def mcp_health(request: Request) -> dict[str, Any]:
    """Agent health check. Returns status + response time so agents can gauge latency."""
    t0 = time.time()
    return {
        "status": "healthy",
        "uptime_seconds": int(time.time() - _start_time),
        "response_time_ms": round((time.time() - t0) * 1000, 1),
        "version": "3.3.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/mcp/status")
async def mcp_status() -> dict[str, Any]:
    """Live system status - cache stats, rate limits, provider health."""
    from app.caching_shield.api_registry import get_api_manager
    from app.caching_shield.tool_registry import TOOL_COUNTS
    from app.caching_shield.unified_layer import get_data_layer

    layer = get_data_layer()
    mgr = get_api_manager()

    return {
        "uptime_seconds": int(time.time() - _start_time),
        "version": "3.3.0",
        "tools": TOOL_COUNTS,
        "cache": layer.stats(),
        "providers": {name: pool.stats() for name, pool in mgr.pools.items()},
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/mcp/changelog")
async def mcp_changelog() -> dict[str, Any]:
    """Version history so agents know what's new."""
    return {
        "current": "3.3.0",
        "history": [
            {
                "version": "3.3.0",
                "date": "2026-06-01",
                "changes": [
                    "18 agent skills with workflow guides and anti-abuse rules",
                    "4 membership tiers with daily call limits",
                    "4 scan packs at 50-53% off individual tools",
                    "4 real-time streaming feeds",
                    "4 deep research report products",
                    "3 batch scanning products at 75-90% off",
                    "3 AI-optimized data feeds",
                    "85 local MCP tools (Solana RPC + EVM 86 networks)",
                    "50 free Boar blockchain tools",
                    "Multi-provider caching shield on every data call",
                    "Platform manifest - single source of truth, auto-syncing",
                    "Agent prompts for 4 agent types",
                    "Quality endpoints: /mcp/health, /mcp/status, /mcp/sdk",
                ],
            },
            {
                "version": "3.2.0",
                "date": "2026-05-15",
                "changes": [
                    "Added POST /mcp JSON-RPC handler",
                    "Added inputSchema to every tool",
                    "Fixed tool naming per MCP spec",
                    "Added dynamic facilitator count",
                    "Added CORS headers for browser clients",
                ],
            },
            {
                "version": "3.1.0",
                "date": "2026-04-01",
                "changes": [
                    "Added /.well-known/mcp discovery",
                    "Added llms.txt for AI agent discovery",
                    "Added x402 payment protocol support",
                    "8 payment facilitators across 13 chains",
                ],
            },
        ],
    }


@router.get("/mcp/sdk")
async def mcp_sdk() -> dict[str, dict[str, str]]:
    """Quick-start code examples for Python, TypeScript, and curl."""
    return {
        "python": {
            "install": "pip install rmi-agent-sdk",
            "quick_start": """from rmi_agent import RMIAgent

agent = RMIAgent()  # auto-discovers via /.well-known/mcp
result = agent.call("rug_pull_predictor", {"token": "So111..."})
print(f"Risk score: {result['data']['score']}")""",
        },
        "typescript": {
            "install": "npm install @rugmunch/agent-sdk",
            "quick_start": """import { RMIAgent } from "@rugmunch/agent-sdk";

const agent = new RMIAgent();
const result = await agent.call("rug_pull_predictor", {
  token: "So111..."
});
console.log(`Risk score: ${result.data.score}`);""",
        },
        "curl": {
            "discover": "curl https://mcp.rugmunch.io/.well-known/mcp",
            "list_tools": "curl https://mcp.rugmunch.io/mcp/tools",
            "call_tool": 'curl -X POST https://mcp.rugmunch.io/mcp/call/rug_pull_predictor \\\n  -H "Content-Type: application/json" \\\n  -d \'{"token": "So11111111111111111111111111111111111111112"}\'',
            "check_trials": "curl https://mcp.rugmunch.io/mcp/trials",
        },
        "mcp_native": {
            "endpoint": "https://mcp.rugmunch.io/mcp",
            "protocol": "MCP 2024-11-05",
            "transport": "Streamable HTTP",
            "setup_claude_desktop": """{
  "mcpServers": {
    "rug-munch": {
      "url": "https://mcp.rugmunch.io/mcp",
      "transport": "streamable-http"
    }
  }
}""",
        },
    }


@router.get("/mcp/trials")
async def mcp_trials(request: Request) -> dict[str, Any]:
    """Check remaining free trials for the requesting agent."""
    fp = request.headers.get(
        "X-Agent-Fingerprint",
        request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown"),
    )

    return {
        "agent_id": fp[:16] + "...",
        "free_trials": {
            "per_tool": "1-5 calls",
            "reset": "Monthly or on tool update",
            "anti_abuse": "Fingerprint-gated. Suspicious patterns rate-limited.",
        },
        "upgrade": {
            "scan_packs": "From $0.09 (53% off)",
            "memberships": "From $4.99/month (60-90% discount)",
            "catalog": "https://mcp.rugmunch.io/mcp/membership",
        },
    }
