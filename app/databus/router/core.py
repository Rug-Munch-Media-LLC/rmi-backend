"""DataBus Router sub-module. Core endpoints."""

from fastapi import APIRouter, HTTPException, Query, Request
from typing import Any
from app.core.logging import get_logger
from app.databus.core import databus

logger = get_logger("databus.router.core")
router = APIRouter(tags=["databus"])

from app.databus.router._shared import verify_admin as _verify_admin
@router.post("/fetch")
async def databus_fetch(request: Request) -> Any:
    """
    Universal data fetch endpoint.
    Every response is packaged based on who's asking.
    No raw data leaks - consumers only get what they're authorized for.

    Body: {
        "data_type": "token_price",         # required
        "admin_key": "",                    # for admin data
        "force_fresh": false,              # skip cache
        "rag_index": false,                # index in RAG
        "consumer_type": "",                # public_web/authenticated/premium/admin/mcp_tool/x402_paid
        "tool_id": "",                      # MCP tool ID for scoping
        "x402_tier": "",                    # x402 pricing tier: free/basic/premium/enterprise
        "mint": "So111...",                 # type-specific params
        "address": "0x...",                 # type-specific params
        ...
    }
    """
    body = await request.json()
    data_type = body.pop("data_type", None)
    if not data_type:
        raise HTTPException(400, "data_type is required")

    admin_key = body.pop("admin_key", "")
    force_fresh = body.pop("force_fresh", False)
    rag_index = body.pop("rag_index", False)
    consumer_type = body.pop("consumer_type", "")
    tool_id = body.pop("tool_id", "")
    x402_tier = body.pop("x402_tier", "")

    result = await databus.fetch(
        data_type=data_type,
        admin_key=admin_key,
        force_fresh=force_fresh,
        rag_index=rag_index,
        consumer_type=consumer_type,
        tool_id=tool_id,
        x402_tier=x402_tier,
        request=request,
        **body,
    )
    if result is None:
        raise HTTPException(502, f"No data available for {data_type}")
    return result

@router.get("/fetch/{data_type}")
async def databus_fetch_get(
    data_type: str,
    request: Request,
    admin_key: str = Query(default=""),
    force_fresh: bool = Query(default=False),
) -> Any:
    """GET convenience for simple queries. Params passed as query args."""
    # Collect all query params except the known ones
    kwargs = {}
    for key, value in request.query_params.items():
        if key not in ("admin_key", "force_fresh"):
            kwargs[key] = value

    result = await databus.fetch(
        data_type=data_type, admin_key=admin_key, force_fresh=force_fresh, **kwargs
    )
    if result is None:
        raise HTTPException(502, f"No data available for {data_type}")
    return result

@router.get("/health")
async def databus_health() -> Any:
    """Full system health: cache, vault, chains, stats."""
    return await databus.health()

@router.get("/capacity")
async def databus_capacity() -> Any:
    """Credit report and recommendations for free tier expansion."""
    if not databus._initialized:
        await databus.initialize()
    return databus.capacity_report()

@router.get("/chains")
async def databus_chains() -> Any:
    """List all available data types and their provider fallback chains."""
    if not databus._initialized:
        await databus.initialize()
    return databus.list_chains()

@router.get("/access-matrix")
async def databus_access_matrix(request: Request) -> Any | dict[str, str]:
    """View the full access control matrix. Admin key required."""
    if not _verify_admin(request):
        from app.databus.access_control import access_controller

        # Non-admin sees a limited view - only their tier's access
        return {
            "note": "Full matrix requires admin key. Your access depends on your consumer type."
        }
    from app.databus.access_control import access_controller

    return access_controller.list_access_matrix()

@router.get("/mcp-scope/{tool_id}")
async def databus_mcp_scope(tool_id: str) -> dict:
    """Get the data types an MCP tool is authorized to access."""
    from app.databus.access_control import access_controller

    allowed = access_controller.get_mcp_allowed_types(tool_id)
    if not allowed:
        return {"tool_id": tool_id, "allowed_types": [], "note": "Unknown tool or no scope defined"}
    return {"tool_id": tool_id, "allowed_types": allowed}

@router.get("/x402-scope/{tier}")
async def databus_x402_scope(tier: str) -> dict:
    """Get the data types an x402 pricing tier can access."""
    from app.databus.access_control import access_controller

    allowed = access_controller.get_x402_allowed_types(tier)
    return {"tier": tier, "allowed_types": allowed}

