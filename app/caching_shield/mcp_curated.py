"""Curated catalogue of free, open-source MCP servers to clone and expose.

These were vetted for: non-overlap with RMI native, MIT/Apache/AGPL license,
active maintenance, real-world value to crypto intelligence users.
"""

from __future__ import annotations

from typing import Any

from app.caching_shield.local_mcp import get_local_mcp

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


async def install_curated_mcps() -> dict[str, Any]:
    """Install all curated free MCPs and discover their tools."""
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


async def call_expanded_tool(
    service: str, tool_name: str, args: dict[str, Any] | None = None, ttl: int = 60
) -> dict[str, Any] | None:
    """Call a tool from a curated MCP service."""
    client = get_local_mcp()
    return await client.call(service, tool_name, args or {}, ttl=ttl)
