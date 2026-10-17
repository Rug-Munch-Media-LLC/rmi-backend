"""
Unified Data Access Layer - Single entry point for ALL tool data calls.

Every x402 tool, MCP tool, scanner, and API endpoint routes through here.
Cache-first, rate-limited, multi-provider fallback. Never hit an API raw.

Architecture:
  Tool call → UnifiedDataLayer → cache check → rate limiter → provider chain → result

No tool anywhere in the system makes a raw HTTP call. Period.
"""

from app.caching_shield.unified.cache import TieredCache
from app.caching_shield.unified.layer import UnifiedDataLayer, get_data_layer
from app.caching_shield.unified.models import ToolResult

__all__ = [
    "TieredCache",
    "ToolResult",
    "UnifiedDataLayer",
    "get_data_layer",
]
