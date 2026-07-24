"""
Canonical Tool Prices — Single Source of Truth

127 tools. Enforcement + databus merged.
This package is THE authoritative list. All endpoints (MCP discovery, x402 catalog,
human marketplace) read from this.

Split from monolithic canonical_tools.py into category files:
  - market_basic.py          basic, market, api, monitoring, bundle
  - security_analysis.py     security, analysis, defi
  - intelligence_premium.py  intelligence, premium, elite, social, launchpad
"""

from app.canonical_tools.market_basic import MARKET_BASIC_TOOLS
from app.canonical_tools.security_analysis import SECURITY_ANALYSIS_TOOLS
from app.canonical_tools.intelligence_premium import INTELLIGENCE_PREMIUM_TOOLS

# Merge all category dicts into the canonical tool prices
CANONICAL_TOOL_PRICES: dict = {}
CANONICAL_TOOL_PRICES.update(MARKET_BASIC_TOOLS)
CANONICAL_TOOL_PRICES.update(SECURITY_ANALYSIS_TOOLS)
CANONICAL_TOOL_PRICES.update(INTELLIGENCE_PREMIUM_TOOLS)

__all__ = ["CANONICAL_TOOL_PRICES"]
