"""Tool execution — /mcp/call/{tool_id}."""

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from typing import Any

from app.config import settings
from app.core.logging import get_logger
from .config import _get_tools, SERVER_NAME, SERVER_VERSION

logger = get_logger("rmi_mcp_v3")
router = APIRouter(tags=["mcp_execution"])


@router.post("/mcp/call/{tool_id}")
async def mcp_call_tool(tool_id: str, request: Request) -> Any:
    """Execute any tool. Requires x402 payment or free trial."""

    data = _get_tools()
    if tool_id not in data["prices"] and tool_id not in ("list", "tools", "catalog"):
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{tool_id}' not found. {len(data['prices'])} tools available -- see /mcp/tools",
        )

    try:
        body = (
            await request.json()
            if request.headers.get("content-type") == "application/json"
            else {}
        )
    except (json.JSONDecodeError, ValueError):
        body = {}

    import httpx

    url = f"{settings.x402_tools_base}/{tool_id}"
    try:
        headers = {}
        for h in (
            "x-pay",
            "X-Pay",
            "X-Device-Id",
            "x-device-id",
            "User-Agent",
            "Authorization",
            "x-wallet-address",
            "X-Wallet-Address",
            "x-turnstile-token",
            "X-Turnstile-Token",
        ):
            val = request.headers.get(h)
            if val:
                headers[h] = val
        if "User-Agent" not in headers:
            headers["User-Agent"] = "RMI-MCP-Proxy/3.1"
        for h in ("X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP"):
            val = request.headers.get(h)
            if val:
                headers[h] = val
        ct = request.headers.get("content-type")
        if ct:
            headers["content-type"] = ct

        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(url, json=body, headers=headers)
            result = (
                resp.json()
                if "application/json" in (resp.headers.get("content-type", ""))
                else {"data": resp.text}
            )
            rh = {}
            for h in (
                "X-RMI-Payment",
                "X-RMI-Trial",
                "X-RMI-Trial-Remaining",
                "X-RMI-Refund-Flagged",
            ):
                if resp.headers.get(h):
                    rh[h] = resp.headers[h]
            rh["Access-Control-Allow-Origin"] = "*"
            return (
                JSONResponse(content=result, status_code=resp.status_code, headers=rh)
                if rh
                else JSONResponse(content=result, status_code=resp.status_code)
            )
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Backend unavailable") from None
    except Exception as e:
        logger.error(f"MCP tool failed: {tool_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Tool execution failed: {str(e)[:200]}") from e
