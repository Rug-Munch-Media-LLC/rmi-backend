"""MCP server bridge provider.

Phase 3B of AUDIT-2026-Q3.md.

Re-exports the MCP bridge provider function from
``app.databus.providers`` for cleaner import paths.
"""

from app.databus.providers import (  # noqa: F401
    _mcp_bridge,
)
