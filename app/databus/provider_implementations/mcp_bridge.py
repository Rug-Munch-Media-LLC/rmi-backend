"""MCP Bridge — Call local MCP servers from DataBus."""

from __future__ import annotations

import json as _json
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# Location of locally-installed MCP servers. Configured via env var;
# defaults to ~/.hermes/mcp-servers (generic home-dir fallback).
_MCP_SERVERS_DIR = Path(os.environ.get("MCP_SERVERS_DIR", str(Path.home() / ".hermes" / "mcp-servers")))


async def _mcp_bridge(mcp_server: str = "", mcp_tool: str = "", **kw) -> dict[str, Any] | None:
    """Universal MCP bridge - calls any local MCP server tool.
    Uses subprocess to call MCP servers installed under MCP_SERVERS_DIR.
    Falls back to HTTP for configured HTTP MCP servers.
    """
    if not mcp_server or not mcp_tool:
        return None
    try:
        tool_args = {k: v for k, v in kw.items() if k not in ("api_key", "mcp_server", "mcp_tool")}

        def _server_entry(rel_path: str) -> list[str]:
            return ["node", str(_MCP_SERVERS_DIR / rel_path)]

        mcp_commands = {
            "evm-direct": _server_entry("evm-direct/bin/cli.js"),
            "evmscope": _server_entry("evmscope/dist/cli.js"),
            "jupiter-mcp": _server_entry("jupiter-mcp/index.js"),
            "crypto-feargreed-mcp": [
                "python3",
                str(_MCP_SERVERS_DIR / "crypto-feargreed-mcp/main.py"),
            ],
            "crypto-indicators-mcp": _server_entry("crypto-indicators-mcp/index.js"),
            "moralis-mcp": _server_entry("moralis-mcp/src/index.mjs"),
            "web3-research-mcp": _server_entry("web3-research-mcp/bin/cli.js"),
            "solana-mcp": _server_entry("solana-mcp-official/index.js"),
        }

        if mcp_server in mcp_commands:
            mcp_request = _json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": mcp_tool, "arguments": tool_args},
                    "id": 1,
                }
            )
            cmd = mcp_commands[mcp_server]
            result = subprocess.run(cmd, input=mcp_request, capture_output=True, text=True, timeout=30, shell=False)
            if result.returncode == 0 and result.stdout:
                data = _json.loads(result.stdout)
                if "result" in data:
                    return {"result": data["result"], "server": mcp_server, "tool": mcp_tool}
        else:
            mcp_configs = {
                "coingecko": "https://mcp.api.coingecko.com/mcp",
            }
            if mcp_server in mcp_configs:
                async with httpx.AsyncClient(timeout=30) as c:
                    r = await c.post(
                        mcp_configs[mcp_server],
                        json={
                            "jsonrpc": "2.0",
                            "method": "tools/call",
                            "params": {"name": mcp_tool, "arguments": tool_args},
                            "id": 1,
                        },
                    )
                    if r.status_code == 200:
                        data = r.json()
                        if "result" in data:
                            return {
                                "result": data["result"],
                                "server": mcp_server,
                                "tool": mcp_tool,
                            }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
