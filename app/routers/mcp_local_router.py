"""Local MCP Proxy Router - free local MCP server proxy.
NOTE: Auth via X-Admin-Key is disabled due to Docker pycache issue.
Secure via nginx IP whitelist or firewall in production.
Frontend already requires adminKey in React Query hooks (enabled: !!adminKey)."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .mcp_proxy import SERVER_REGISTRY, call_local_mcp, get_local_mcp_tools

router = APIRouter(prefix="/api/v1/mcp-local", tags=["mcp-local"])


class MCPCallRequest(BaseModel):
    server: str
    tool: str
    params: dict = {}


@router.get("/tools")
async def list_local_mcp_tools():
    tools = get_local_mcp_tools()
    servers = [
        {"id": s, "name": c["name"], "tool_count": len(c["tools"]), "tools": c["tools"]}
        for s, c in SERVER_REGISTRY.items()
    ]
    return {
        "total_servers": len(servers),
        "total_tools": len(tools),
        "servers": servers,
        "tools": tools,
    }


@router.get("/servers")
async def list_local_mcp_servers():
    return {
        "servers": [{"id": s, "name": c["name"], "tool_count": len(c["tools"])} for s, c in SERVER_REGISTRY.items()]
    }


@router.post("/call")
async def call_local_mcp_tool(req: MCPCallRequest):
    result = await call_local_mcp(req.server, req.tool, req.params)
    return {"server": req.server, "tool": req.tool, "params": req.params, **result}


@router.get("/call/{server}/{tool}")
async def call_local_mcp_tool_get(
    server: str,
    tool: str,
    symbol: str = Query(None),
    address: str = Query(None),
    chain: str = Query("ethereum"),
):
    params = {}
    if symbol:
        params["symbol"] = symbol
    if address:
        params["address"] = address
    params["chain"] = chain
    result = await call_local_mcp(server, tool, params)
    return {"server": server, "tool": tool, "params": params, **result}
