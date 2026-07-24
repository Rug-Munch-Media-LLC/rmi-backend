"""
MCP Bridge — call any local/remote MCP server tool from DataBus.

Uses subprocess to invoke opencode MCP tools and returns parsed results.
"""

import json
import subprocess

from app.core.logging import get_logger

logger = get_logger("databus.providers.mcp_bridge")


async def _mcp_bridge(mcp_server: str = "", mcp_tool: str = "", **kw) -> dict | None:
    """Universal MCP bridge — calls any local/remote MCP server tool.

    Args:
        mcp_server: Name of the MCP server to call
        mcp_tool: Name of the tool to invoke
        **kw: Additional keyword arguments passed as tool parameters

    Returns:
        Parsed JSON result from the MCP tool, or None on failure.
    """
    if not mcp_server or not mcp_tool:
        return None
    try:
        # Build args from kwargs
        args = []
        for key, value in kw.items():
            if key in ("api_key", "vault", "cache", "mcp_server", "mcp_tool"):
                continue
            if isinstance(value, str):
                args.extend(["--" + key, value])
            else:
                args.extend(["--" + key, json.dumps(value)])

        result = subprocess.run(
            ["opencode", "mcp", "call", mcp_server, mcp_tool, *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        logger.warning(f"MCP call failed: {result.stderr[:200]}")
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"MCP bridge error: {e}")
    except subprocess.TimeoutExpired:
        logger.warning("MCP bridge timeout")
    except json.JSONDecodeError:
        logger.warning("MCP bridge: invalid JSON response")
    return None
