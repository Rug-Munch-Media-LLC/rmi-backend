"""T33 MCP Server - HTTP wrapper for SSE transport.

Per v4.0 §T33. Endpoints:
  POST /mcp                  JSON-RPC 2.0 endpoint
  GET  /mcp/tools            Tool catalog
  POST /mcp/call/{tool_id}   Direct tool execution (no JSON-RPC)

The server speaks the Model Context Protocol natively. Claude Desktop
and Cursor connect via:
  {"mcpServers": {"rugmunch": {"url": "https://mcp.rugmunch.io/mcp", "transport": "sse"}}}
"""
from __future__ import annotations

import json
from app.core.logging import get_logger
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.mcp.server import (
    MCP_PROTOCOL_VERSION,
    MCP_SERVER_VERSION,
    TOOL_CATALOG,
    TOOL_DEPRECATED,
    TOOL_SUCCESSORS,
    TOOL_VERSIONS,
    call_tool,
)

router = APIRouter(prefix="/mcp", tags=["mcp"])
log = get_logger(__name__)


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: dict[str, Any] = {}
    id: int | str | None = None


class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    result: Any | None = None
    error: dict | None = None
    id: int | str | None = None


@router.post("")
async def jsonrpc_handler(req: JsonRpcRequest) -> dict:
    """JSON-RPC 2.0 endpoint for MCP clients.

    Methods:
      - initialize           → returns server info
      - tools/list            → returns tool catalog
      - tools/call            → dispatches to backend
      - resources/list        → empty
      - prompts/list          → empty
    """
    if req.jsonrpc != "2.0":
        return {"jsonrpc": "2.0", "error": {"code": -32600, "message": "invalid jsonrpc version"}, "id": req.id}

    if req.method == "initialize":
        return {
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "rugmunch-intelligence",
                    "version": MCP_SERVER_VERSION,
                    "description": "Crypto intelligence platform - 13+ chains, 8 MCP tools, x402 paid tier",
                },
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            },
            "id": req.id,
        }

    if req.method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "result": {"tools": TOOL_CATALOG},
            "id": req.id,
        }

    if req.method == "tools/call":
        name = req.params.get("name", "")
        arguments = req.params.get("arguments", {})
        if not name:
            return {"jsonrpc": "2.0", "error": {"code": -32602, "message": "tool name required"}, "id": req.id}
        result = await call_tool(name, arguments)
        return {
            "jsonrpc": "2.0",
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, default=str)[:50000]}],
                "isError": "error" in result,
            },
            "id": req.id,
        }

    if req.method == "resources/list":
        return {"jsonrpc": "2.0", "result": {"resources": []}, "id": req.id}

    if req.method == "prompts/list":
        return {"jsonrpc": "2.0", "result": {"prompts": []}, "id": req.id}

    if req.method == "notifications/initialized":
        return {"jsonrpc": "2.0", "result": {}, "id": req.id}

    return {
        "jsonrpc": "2.0",
        "error": {"code": -32601, "message": f"method not found: {req.method}"},
        "id": req.id,
    }


@router.get("/tools")
async def list_tools() -> dict:
    """Plain JSON endpoint (for direct integration, no JSON-RPC)."""
    return {"server": "rugmunch-intelligence", "version": MCP_SERVER_VERSION, "tools": TOOL_CATALOG}


@router.get("/info")
async def server_info() -> dict:
    """Server metadata: version, protocol, tool count, capabilities.

    Use this to discover the MCP server's capabilities without listing all tools.
    Equivalent to MCP initialize handshake.
    """
    return {
        "server": "rugmunch-intelligence",
        "server_version": MCP_SERVER_VERSION,
        "protocol_version": MCP_PROTOCOL_VERSION,
        "tool_count": len(TOOL_CATALOG),
        "tools_versioned": sum(1 for t in TOOL_CATALOG if t["name"] in TOOL_VERSIONS),
        "tools_deprecated": list(TOOL_DEPRECATED),
        "tools_with_successors": list(TOOL_SUCCESSORS.keys()),
        "capabilities": ["tools", "resources", "prompts"],
        "endpoints": {
            "jsonrpc": "/mcp",
            "tools_list": "/mcp/tools",
            "server_info": "/mcp/info",
            "direct_call": "/mcp/call/{tool_id}",
        },
        "auth": {
            "free_tier_daily": 5,
            "pro_tier": "x402 micropayment per call",
            "x402_endpoint": "https://x402.rugmunch.io",
        },
        "links": {
            "homepage": "https://rugmunch.io",
            "mcp_endpoint": "https://mcp.rugmunch.io/mcp",
            "status": "https://status.rugmunch.io",
            "docs": "https://github.com/Rug-Munch-Media-LLC/rmi-docs",
        },
    }


@router.post("/call/{tool_id}")
async def direct_call(tool_id: str, request: Request) -> dict:
    """Direct tool execution (no JSON-RPC). For curl/scripts."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    arguments = body.get("arguments", body) if isinstance(body, dict) else {}
    result = await call_tool(tool_id, arguments)
    return result
