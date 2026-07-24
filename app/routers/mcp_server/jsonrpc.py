"""MCP JSON-RPC endpoint — POST /mcp (Streamable HTTP)."""

from __future__ import annotations
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from typing import Any
from app.config import settings
from app.core.logging import get_logger
from .config import (
    _get_tools,
    MCP_PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
)

logger = get_logger("rmi_mcp_v3")
router = APIRouter(tags=["mcp_jsonrpc"])

@router.post("/mcp")
async def mcp_jsonrpc(request: Request) -> Any:
    """MCP Streamable HTTP transport - handle JSON-RPC requests.
    Methods: initialize, tools/list, tools/call, resources/list, prompts/list, ping
    """

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None,
            },
        )

    method = body.get("method", "")
    req_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": True},
                        "resources": {},
                        "prompts": {},
                    },
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }
        )

    if method == "ping":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})

    if method == "tools/list":
        from .catalog import _build_tools_list

        tools_data, _cats, _chains_data, _fac_count = _build_tools_list()
        tools_list = []
        for tool_id, info in tools_data.items():
            category = info.get("category", "analysis")
            dot_name = f"{category}.{tool_id}"
            tools_list.append(
                {
                    "name": dot_name,
                    "description": info["description"],
                    "inputSchema": info["inputSchema"],
                    "outputSchema": {
                        "type": "object",
                        "properties": {
                            "result": {
                                "type": "object",
                                "description": "Tool-specific result data",
                            },
                            "status": {"type": "string", "enum": ["ok", "error", "no_data"]},
                            "error": {"type": "string"},
                            "metadata": {
                                "type": "object",
                                "properties": {
                                    "tool": {"type": "string"},
                                    "chain": {"type": "string"},
                                    "elapsed_ms": {"type": "number"},
                                    "trial_used": {"type": "boolean"},
                                },
                            },
                        },
                        "required": ["result", "status"],
                    },
                    "annotations": {
                        "title": info.get("display_name", info["name"].replace("_", " ").title()),
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": True,
                        "openWorldHint": True,
                        "category": info["category"],
                        "price_usd": info["price_usd"],
                        "trial_free": info["trial_free"],
                        "chains": info["chains"],
                    },
                }
            )
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tools_list},
            }
        )

    if method == "resources/list":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}})

    if method == "prompts/list":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}})

    if method == "ai.smithery/events/list":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"events": []}})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        internal_name = tool_name
        if "." in tool_name:
            internal_name = tool_name.split(".", 1)[1]
            data_check = _get_tools()
            if internal_name not in data_check["prices"] and tool_name in data_check["prices"]:
                internal_name = tool_name

        data = _get_tools()
        if internal_name not in data["prices"]:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found."},
                }
            )

        import httpx

        url = f"{settings.x402_tools_base}/{internal_name}"
        headers = {"Content-Type": "application/json", "User-Agent": "RMI-MCP-JSONRPC/3.1"}
        for h in (
            "x-pay",
            "X-Pay",
            "X-Device-Id",
            "x-device-id",
            "Authorization",
            "x-wallet-address",
            "X-Wallet-Address",
            "x-turnstile-token",
            "X-Turnstile-Token",
        ):
            val = request.headers.get(h)
            if val:
                headers[h] = val
        for h in ("X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP"):
            val = request.headers.get(h)
            if val:
                headers[h] = val

        try:
            async with httpx.AsyncClient(timeout=45) as client:
                resp = await client.post(url, json=arguments, headers=headers)
                if resp.status_code == 402:
                    result = resp.json()
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [{"type": "text", "text": json.dumps(result)}],
                                "isError": False,
                            },
                        }
                    )
                result = (
                    resp.json()
                    if "application/json" in (resp.headers.get("content-type", ""))
                    else {"data": resp.text}
                )
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(result)}],
                            "isError": resp.status_code >= 400,
                        },
                    }
                )
        except httpx.ConnectError:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": "Backend unavailable"},
                }
            )
        except Exception as e:
            logger.error(f"MCP JSON-RPC tools/call failed: {tool_name}: {e}")
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": f"Tool execution failed: {str(e)[:200]}"},
                }
            )

    return JSONResponse(
        status_code=400,
        content={
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        },
    )
