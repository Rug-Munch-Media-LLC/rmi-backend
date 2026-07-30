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

import asyncio
import hashlib
import json
import subprocess
import time
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger("local_mcp")

MCP_HOME = Path(os.getenv("MCP_HOME", "/path/to/mcp-servers"))


class LocalMCPClient:
    """Pulls MCP servers from GitHub, runs them locally via stdio, caches results."""

    def __init__(self) -> None:
        MCP_HOME.mkdir(parents=True, exist_ok=True)
        self._servers: dict[str, dict] = {}  # name -> {repo, process, tools}
        self._l1: dict[str, tuple] = {}

    async def install(self, repo: str, name: str | None = None) -> dict:
        """Clone an MCP server from GitHub and discover its tools."""
        name = name or repo.split("/")[-1]
        server_dir = MCP_HOME / name

        # Clone if not exists
        if not server_dir.exists():
            r = subprocess.run(
                ["git", "clone", f"https://github.com/{repo}.git", str(server_dir)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if r.returncode != 0:
                raise RuntimeError(f"Clone failed: {r.stderr[:200]}")

        # Detect package manager and install deps
        if (server_dir / "package.json").exists():
            subprocess.run(["npm", "install", "--production"], cwd=server_dir, capture_output=True, timeout=60)
        elif (server_dir / "requirements.txt").exists():
            subprocess.run(
                ["pip", "install", "-r", "requirements.txt"],
                cwd=server_dir,
                capture_output=True,
                timeout=60,
            )

        # Discover tools via MCP stdio handshake
        tools = await self._discover_tools(name, server_dir)
        self._servers[name] = {"repo": repo, "dir": str(server_dir), "tools": tools}

        logger.info(f"MCP {name}: {len(tools)} tools from {repo}")
        return {"name": name, "tools": len(tools), "repo": repo}

    async def _discover_tools(self, name: str, server_dir: Path) -> list[dict]:
        """Run MCP server and call tools/list to discover available tools."""
        entry = self._find_entrypoint(server_dir)
        if not entry:
            return []

        try:
            # Start MCP server as subprocess
            proc = await asyncio.create_subprocess_exec(
                *entry["command"],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=server_dir,
            )

            # MCP initialize
            init_msg = (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "rmi-caching-shield", "version": "1.0"},
                        },
                    }
                )
                + "\n"
            )

            proc.stdin.write(init_msg.encode())
            await proc.stdin.drain()

            # Read response
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
            json.loads(line.decode())

            # Send initialized notification
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + b"\n")
            await proc.stdin.drain()

            # List tools
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + b"\n")
            await proc.stdin.drain()
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
            tools_resp = json.loads(line.decode())

            tools = tools_resp.get("result", {}).get("tools", [])

            # Keep process alive for future calls
            self._servers[name] = self._servers.get(name, {})
            self._servers[name]["process"] = proc

            proc.stdin.close()
            return tools
        except Exception as e:
            logger.warning(f"MCP discover {name} failed: {e}")
            return []

    def _find_entrypoint(self, server_dir: Path) -> dict | None:
        """Find how to start the MCP server."""
        # Check package.json for bin/main
        pkg = server_dir / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                if data.get("main"):
                    return {"command": ["node", data["main"]]}
                if data.get("bin"):
                    bin_val = data["bin"]
                    if isinstance(bin_val, str):
                        return {"command": ["node", bin_val]}
                    if isinstance(bin_val, dict):
                        return {"command": ["node", next(iter(bin_val.values()))]}
            except json.JSONDecodeError:
                pass

        # Check for Python entrypoint
        for entry in ["server.py", "mcp_server.py", "main.py", "__main__.py"]:
            if (server_dir / entry).exists():
                return {"command": ["python3", entry]}

        # Check for TypeScript entry
        for entry in ["src/index.ts", "src/server.ts", "index.ts"]:
            if (server_dir / entry).exists():
                return {"command": ["npx", "tsx", entry]}

        return None

    async def call(self, server: str, tool: str, args: dict, ttl: int = 60) -> dict | None:
        """Call an MCP tool through caching shield."""
        cache_key = hashlib.sha256(f"mcp:{server}:{tool}:{json.dumps(args, sort_keys=True)}".encode()).hexdigest()[:24]

        # L1 cache
        entry = self._l1.get(cache_key)
        if entry:
            expiry, data = entry
            if time.monotonic() < expiry:
                return data
            del self._l1[cache_key]

        svr = self._servers.get(server, {})
        proc = svr.get("process")

        if not proc:
            return None

        try:
            req = (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 99,
                        "method": "tools/call",
                        "params": {"name": tool, "arguments": args},
                    }
                )
                + "\n"
            )
            proc.stdin.write(req.encode())
            await proc.stdin.drain()
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            resp = json.loads(line.decode())
            result = resp.get("result", {}).get("content", [{}])[0].get("text", "")
            data = json.loads(result) if result else None
            if data:
                self._l1[cache_key] = (time.monotonic() + ttl, data)
            return data
        except Exception as e:
            logger.debug(f"MCP call {server}/{tool}: {e}")
            return None

    def list_servers(self) -> list[dict]:
        return [{"name": n, "repo": s["repo"]} for n, s in self._servers.items()]

    def stats(self) -> dict:
        return {"servers": len(self._servers), "cache_entries": len(self._l1)}


_mcp_client: LocalMCPClient | None = None


def get_local_mcp() -> LocalMCPClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = LocalMCPClient()
    return _mcp_client


# Curated list of free, open-source MCPs to clone and expose.
# These were vetted for: non-overlap with RMI native, MIT/Apache/AGPL license,
# active maintenance, real-world value to crypto intelligence users.
EXPANDED_MCP_REPOS = {
    "fear-greed-mcp": {
        "repo": "kukapay/crypto-feargreed-mcp",
        "service": "fear-greed-mcp",
        "category": "sentiment",
        "license": "MIT",
        "description": "Crypto Fear & Greed Index via Alternative.me",
    },
    "crypto-indicators-mcp": {
        "repo": "kukapay/crypto-indicators-mcp",
        "service": "crypto-indicators-mcp",
        "category": "analysis",
        "license": "MIT",
        "description": "50+ technical analysis indicators",
    },
    "openzeppelin-wizard": {
        "repo": "OpenZeppelin/contracts-wizard",
        "service": "openzeppelin-wizard",
        "category": "security",
        "license": "AGPL-3.0",
        "description": "OpenZeppelin audited smart contract generator",
    },
    "web3-research-mcp": {
        "repo": "aaronjmars/web3-research-mcp",
        "service": "web3-research-mcp",
        "category": "research",
        "license": "MIT",
        "description": "Local web research: CoinGecko + DefiLlama + URL fetcher",
    },
}


async def install_curated_mcps() -> dict:
    """Install all curated free MCPs from GitHub and discover their tools.

    Returns summary: {server_name: {repo, tools_count, status, error}}
    """
    client = get_local_mcp()
    results = {}

    for name, meta in EXPANDED_MCP_REPOS.items():
        try:
            info = await client.install(meta["repo"], name=name)
            results[name] = {
                "repo": meta["repo"],
                "service": meta["service"],
                "license": meta["license"],
                "tools_count": info.get("tools", 0),
                "status": "ok",
            }
        except Exception as e:
            results[name] = {
                "repo": meta["repo"],
                "service": meta["service"],
                "license": meta["license"],
                "tools_count": 0,
                "status": "error",
                "error": str(e)[:200],
            }

    return results


async def call_expanded_tool(service: str, tool_name: str, args: dict | None = None, ttl: int = 60) -> dict | None:
    """Call a tool from a curated MCP service."""
    args = args or {}
    client = get_local_mcp()
    return await client.call(service, tool_name, args, ttl=ttl)
