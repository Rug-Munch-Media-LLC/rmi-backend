"""
Rug Munch Intelligence MCP Server v3.1 — Split into focused modules.
Maintains backward compatibility: from app.routers.mcp_server import router
"""

from __future__ import annotations

from fastapi import APIRouter

# Create the main router
router = APIRouter(tags=["mcp"])

# Import and include all sub-routers
from .catalog import router as catalog_router  # noqa: E402 — module docstring precedes import block
from .discovery import router as discovery_router  # noqa: E402 — module docstring precedes import block
from .earnings import router as earnings_router  # noqa: E402 — module docstring precedes import block
from .execution import router as execution_router  # noqa: E402 — module docstring precedes import block
from .jsonrpc import router as jsonrpc_router  # noqa: E402 — module docstring precedes import block
from .news import router as news_router  # noqa: E402 — module docstring precedes import block
from .platform import router as platform_router  # noqa: E402 — module docstring precedes import block
from .social_news import router as social_router  # noqa: E402 — module docstring precedes import block

router.include_router(discovery_router)
router.include_router(catalog_router)
router.include_router(jsonrpc_router)
router.include_router(execution_router)
router.include_router(platform_router)
router.include_router(earnings_router)
router.include_router(news_router)
router.include_router(social_router)

# Re-export config functions for backward compatibility
from .config import (  # noqa: E402 — module docstring precedes import block
    MCP_PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    _desc,
    _get_facilitator_count,
    _get_tools,
    _input_schema,
)

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "_desc",
    "_get_facilitator_count",
    "_get_tools",
    "_input_schema",
    "router",
]
