"""
Rug Munch Intelligence MCP Server v3.1 — Split into focused modules.
Maintains backward compatibility: from app.routers.mcp_server import router
"""

from __future__ import annotations
from fastapi import APIRouter

# Create the main router
router = APIRouter(tags=["mcp"])

# Import and include all sub-routers
from .discovery import router as discovery_router
from .catalog import router as catalog_router
from .jsonrpc import router as jsonrpc_router
from .execution import router as execution_router
from .platform import router as platform_router
from .earnings import router as earnings_router
from .news import router as news_router
from .social_news import router as social_router

router.include_router(discovery_router)
router.include_router(catalog_router)
router.include_router(jsonrpc_router)
router.include_router(execution_router)
router.include_router(platform_router)
router.include_router(earnings_router)
router.include_router(news_router)
router.include_router(social_router)

# Re-export config functions for backward compatibility
from .config import (
    MCP_PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    _get_tools,
    _get_facilitator_count,
    _desc,
    _input_schema,
)

__all__ = [
    "router",
    "MCP_PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "_get_tools",
    "_get_facilitator_count",
    "_desc",
    "_input_schema",
]
