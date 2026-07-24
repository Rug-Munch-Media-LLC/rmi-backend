"""MCP Bridge — Call local MCP servers from DataBus."""

from __future__ import annotations
import json as _json
import subprocess

import httpx

from sdks.python.x402_frameworks.autogen_adapter import logger

async def _mcp_bridge(mcp_server: str = "", mcp_tool: str = "", **kw) -> dict | None:
    """Universal MCP bridge - calls any local MCP server tool.
    Uses subprocess to call MCP servers installed in /root/.hermes/mcp-servers/.
    Falls back to HTTP for configured HTTP MCP servers.
    """
    if not mcp_server or not mcp_tool:
        return None
    try:
        tool_args = {k: v for k, v in kw.items() if k not in ("api_key", "mcp_server", "mcp_tool")}

        MCP_COMMANDS = {
            "evm-direct": ["node", "/root/.hermes/mcp-servers/evm-direct/bin/cli.js"],
            "evmscope": ["node", "/root/.hermes/mcp-servers/evmscope/dist/cli.js"],
            "jupiter-mcp": ["node", "/root/.hermes/mcp-servers/jupiter-mcp/index.js"],
            "crypto-feargreed-mcp": [
                "python3",
                "/root/.hermes/mcp-servers/crypto-feargreed-mcp/main.py",
            ],
            "crypto-indicators-mcp": [
                "node",
                "/root/.hermes/mcp-servers/crypto-indicators-mcp/index.js",
            ],
            "moralis-mcp": ["node", "/root/.hermes/mcp-servers/moralis-mcp/src/index.mjs"],
            "web3-research-mcp": ["node", "/root/.hermes/mcp-servers/web3-research-mcp/bin/cli.js"],
            "solana-mcp": ["node", "/root/.hermes/mcp-servers/solana-mcp-official/index.js"],
        }

        if mcp_server in MCP_COMMANDS:
            mcp_request = _json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": mcp_tool, "arguments": tool_args},
                    "id": 1,
                }
            )
            cmd = MCP_COMMANDS[mcp_server]
            result = subprocess.run(
                cmd, input=mcp_request, capture_output=True, text=True, timeout=30
            )
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
