"""
Expanded MCP Tools Catalog — pricing and descriptions for all tools.

This package splits the original monolithic dict into:
- core_tools.py: Base tool definitions
- chain_variants.py: Chain-specific tool variants
- sentinel_modules.py: SENTINEL scanner module definitions
"""

from __future__ import annotations

from app.routers._expanded_tools.chain_variants import CHAIN_VARIANTS
from app.routers._expanded_tools.core_tools import CORE_TOOLS
from app.routers._expanded_tools.sentinel_modules import SENTINEL_MODULES

ADDITIONAL_TOOLS = {**CORE_TOOLS, **CHAIN_VARIANTS, **SENTINEL_MODULES}

__all__ = ["ADDITIONAL_TOOLS", "CHAIN_VARIANTS", "CORE_TOOLS", "SENTINEL_MODULES"]
