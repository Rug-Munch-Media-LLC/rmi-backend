"""
Local MCP Client - Pull open-source MCP servers from GitHub, run locally, route through caching shield.

Architecture:
  GitHub MCP repos → local clone → stdio transport → cache wrapper → our tooling

One layer. No Smithery. No cloud gateways. Just git + stdio + cache.

Usage:
    from app.caching_shield.local_mcp import LocalMCPClient
    client = LocalMCPClient()

    # Pull and run an MCP server from GitHub
    await client.install("boar-network/blockchain-mcp")
    result = await client.call("boar", "eth_getBalance", {"address": "0x..."})
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from app.caching_shield.mcp_stdio import call_tool, discover_tools
from app.core.logging import get_logger

logger = get_logger("local_mcp")

MCP_HOME = Path(os.getenv("MCP_HOME", "/path/to/mcp-servers"))


class LocalMCPClient:
    """Pulls MCP servers from GitHub, runs them locally via stdio, caches results."""

    def __init__(self) -> None:
        MCP_HOME.mkdir(parents=True, exist_ok=True)
        self._servers: dict[str, dict[str, Any]] = {}  # name -> {repo, process, tools}
        self._l1: dict[str, tuple[Any, ...]] = {}

    async def install(self, repo: str, name: str | None = None) -> dict[str, Any]:
        """Clone an MCP server from GitHub and discover its tools."""
        name = name or repo.split("/")[-1]
        server_dir = MCP_HOME / name

        if not server_dir.exists():
            r = subprocess.run(
                ["git", "clone", f"https://github.com/{repo}.git", str(server_dir)],
                capture_output=True,
                text=True,
                timeout=30,
                shell=False,
            )
            if r.returncode != 0:
                raise RuntimeError(f"Clone failed: {r.stderr[:200]}")

        if (server_dir / "package.json").exists():
            subprocess.run(
                ["npm", "install", "--production"], cwd=server_dir, capture_output=True, timeout=60, shell=False
            )
        elif (server_dir / "requirements.txt").exists():
            subprocess.run(
                ["pip", "install", "-r", "requirements.txt"],
                cwd=server_dir,
                capture_output=True,
                timeout=60,
                shell=False,
            )

        tools, proc = await discover_tools(name, server_dir)
        self._servers[name] = {"repo": repo, "dir": str(server_dir), "tools": tools}
        if proc is not None:
            self._servers[name]["process"] = proc

        logger.info(f"MCP {name}: {len(tools)} tools from {repo}")
        return {"name": name, "tools": len(tools), "repo": repo}

    async def call(self, server: str, tool: str, args: dict[str, Any], ttl: int = 60) -> dict[str, Any] | None:
        """Call an MCP tool through caching shield."""
        cache_key = hashlib.sha256(f"mcp:{server}:{tool}:{json.dumps(args, sort_keys=True)}".encode()).hexdigest()[:24]

        entry = self._l1.get(cache_key)
        if entry:
            expiry, data = entry
            if time.monotonic() < expiry:
                return data
            del self._l1[cache_key]

        proc = self._servers.get(server, {}).get("process")
        if not proc:
            return None

        data = await call_tool(proc, tool, args)
        if data:
            self._l1[cache_key] = (time.monotonic() + ttl, data)
        return data

    def list_servers(self) -> list[dict[str, Any]]:
        return [{"name": n, "repo": s["repo"]} for n, s in self._servers.items()]

    def stats(self) -> dict[str, Any]:
        return {"servers": len(self._servers), "cache_entries": len(self._l1)}


_mcp_client: LocalMCPClient | None = None


def get_local_mcp() -> LocalMCPClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = LocalMCPClient()
    return _mcp_client
