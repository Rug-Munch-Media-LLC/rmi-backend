"""MCP stdio transport: entrypoint detection, initialize handshake, tool calls.

Split out of ``local_mcp`` so the client facade stays small and the wire protocol
lives in one place — including the protocol revision, which comes from the shared
``MCP_PROTOCOL_VERSION`` constant and is never hardcoded here.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.mcp._catalog import MCP_PROTOCOL_VERSION

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger("mcp_stdio")

CLIENT_INFO = {"name": "rmi-caching-shield", "version": "1.0"}


def find_entrypoint(server_dir: Path) -> dict[str, Any] | None:
    """Find how to start an MCP server from its cloned directory."""
    pkg = server_dir / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            if data.get("main"):
                return {"command": ["node", data["main"]]}
            bin_val = data.get("bin")
            if isinstance(bin_val, str):
                return {"command": ["node", bin_val]}
            if isinstance(bin_val, dict):
                return {"command": ["node", next(iter(bin_val.values()))]}
        except json.JSONDecodeError as e:
            logger.warning(f"find_entrypoint failed: {e}")

    for entry in ("server.py", "mcp_server.py", "main.py", "__main__.py"):
        if (server_dir / entry).exists():
            return {"command": ["python3", entry]}

    for entry in ("src/index.ts", "src/server.ts", "index.ts"):
        if (server_dir / entry).exists():
            return {"command": ["npx", "tsx", entry]}

    return None


async def _exchange(proc: Any, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Write one JSON-RPC message and read one line back."""
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    await proc.stdin.drain()
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    return json.loads(line.decode())


async def discover_tools(name: str, server_dir: Path) -> tuple[list[dict[str, Any]], Any | None]:
    """Spawn the server, run the MCP handshake, and return its tools plus the live process."""
    entry = find_entrypoint(server_dir)
    if not entry:
        return [], None

    try:
        proc = await asyncio.create_subprocess_exec(
            *entry["command"],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=server_dir,
        )
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError(f"MCP {name}: stdio pipes unavailable")

        await _exchange(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
            },
            timeout=10,
        )
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode() + b"\n")
        await proc.stdin.drain()

        resp = await _exchange(
            proc,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout=10,
        )
        tools = resp.get("result", {}).get("tools", [])
        proc.stdin.close()
        return tools, proc
    except Exception as e:
        logger.warning(f"MCP discover {name} failed: {e}")
        return [], None


async def call_tool(proc: Any, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Invoke one tool over an initialized stdio process."""
    try:
        resp = await _exchange(
            proc,
            {"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {"name": tool, "arguments": args}},
            timeout=30,
        )
        result = resp.get("result", {}).get("content", [{}])[0].get("text", "")
        return json.loads(result) if result else None
    except Exception as e:
        logger.debug(f"MCP call {tool}: {e}")
        return None
