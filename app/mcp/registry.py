"""MCP Tool Registry — resolves and lists tools from the TOOL_CATALOG."""

from __future__ import annotations

import logging

from app.mcp.manifest import MCPServerManifest, MCPToolManifest
from app.mcp.server import (
    TOOL_CATALOG,
    TOOL_DEPRECATED,
    TOOL_SUCCESSORS,
    TOOL_VERSIONS,
)

log = logging.getLogger(__name__)

# O(1) lookup index built once at module load
_TOOL_BY_NAME: dict[str, dict] = {t["name"]: t for t in TOOL_CATALOG}

# Synthetic auth_scope mapping for each tool (gopass path)
TOOL_AUTH_SCOPES: dict[str, str] = {
    "get_token_risk": "rmi/api/free",
    "get_wallet_analysis": "rmi/api/free",
    "get_deployer_reputation": "rmi/api/free",
    "get_news_sentiment": "rmi/api/free",
    "generate_report": "rmi/api/x402",
    "query_catalog": "rmi/api/x402",
    "find_similar_tokens": "rmi/api/x402",
    "resolve_entity": "rmi/api/x402",
    "analytics_query": "rmi/api/internal",
    "eth_labels_query": "rmi/api/internal",
    "eth_labels_stats": "rmi/api/internal",
    "mcp_discover": "public",
    "status_check": "public",
}

# Category tags per tool (used by list_tools(category=...))
TOOL_CATEGORIES: dict[str, list[str]] = {
    "get_token_risk": ["free", "tier-mix"],
    "get_wallet_analysis": ["free", "tier-mix"],
    "get_deployer_reputation": ["free", "tier-mix"],
    "get_news_sentiment": ["free", "tier-mix"],
    "generate_report": ["pro", "x402", "paid"],
    "query_catalog": ["pro", "x402", "paid"],
    "find_similar_tokens": ["pro", "x402", "paid"],
    "resolve_entity": ["pro", "x402", "paid"],
    "analytics_query": ["moat", "internal", "tier1"],
    "eth_labels_query": ["moat", "internal", "tier2"],
    "eth_labels_stats": ["moat", "internal", "tier2"],
    "mcp_discover": ["public", "meta"],
    "status_check": ["public", "meta"],
}

__all__ = ["get_server_manifest", "list_tools", "resolve_tool"]


def _extract_server(tool_name: str) -> str:
    """Extract server slug from tool name (split on ':')."""
    return tool_name.split(":", 1)[0] if ":" in tool_name else "rmi"


def _parse_version(version: str) -> tuple[int, int, int]:
    """Parse semver MAJOR.MINOR.PATCH → tuple of ints."""
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid semver: {version!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _match_category(description: str, category: str) -> bool:
    """Case-insensitive substring match for category hint in description."""
    return category.lower() in description.lower()


def _qualify_name(tool_name: str) -> str:
    """Add the server prefix required by MCPToolManifest pattern.

    Pattern: ^[a-z][a-z0-9-]*:[a-z][a-z0-9_]*$
    Bare names like "get_token_risk" become "rmi:get_token_risk".
    """
    return tool_name if ":" in tool_name else f"rmi:{tool_name}"


def _dict_to_manifest(tool_name: str, auth_scope: str) -> MCPToolManifest:
    """Convert a TOOL_CATALOG entry to MCPToolManifest (O(1) via _TOOL_BY_NAME)."""
    entry = _TOOL_BY_NAME[tool_name]
    return MCPToolManifest(
        name=_qualify_name(tool_name),
        version=TOOL_VERSIONS.get(tool_name, "1.0.0"),
        server=_extract_server(tool_name),
        description=entry.get("description", "")[:200],
        input_schema=entry.get("inputSchema", {}),
        output_schema={},
        auth_scope=auth_scope,
        deprecated=tool_name in TOOL_DEPRECATED,
        successor=(_qualify_name(TOOL_SUCCESSORS[tool_name]) if tool_name in TOOL_SUCCESSORS else None),
    )


async def resolve_tool(
    name: str,
    major_version: int | None = None,
    include_deprecated: bool = False,
) -> MCPToolManifest | None:
    """Resolve a tool name + optional MAJOR version to its full manifest.

    Returns None if not found, version mismatch, or deprecated without successor.
    """
    if name not in _TOOL_BY_NAME:
        log.warning("Tool not found in registry: %s", name)
        return None

    if major_version is not None:
        try:
            tool_major = _parse_version(TOOL_VERSIONS.get(name, "0.0.0"))[0]
        except ValueError as exc:
            log.warning("Bad version for %s: %s", name, exc)
            return None
        if tool_major != major_version:
            log.warning(
                "Version mismatch for %s: wanted MAJOR %d, got %d",
                name,
                major_version,
                tool_major,
            )
            return None

    if name in TOOL_DEPRECATED and not include_deprecated:
        successor = TOOL_SUCCESSORS.get(name)
        if successor:
            return await resolve_tool(successor, major_version=major_version, include_deprecated=False)
        log.warning("Tool %s is deprecated with no successor", name)
        return None

    return _dict_to_manifest(name, TOOL_AUTH_SCOPES.get(name, "public"))


async def list_tools(
    server: str | None = None,
    auth_scope: str | None = None,
    include_deprecated: bool = False,
    category: str | None = None,
) -> list[MCPToolManifest]:
    """List tools matching optional filters."""
    results: list[MCPToolManifest] = []

    for tool_name in _TOOL_BY_NAME:
        # Server prefix filter
        if server is not None and _extract_server(tool_name) != server:
            continue

        # Auth scope filter
        tool_auth_scope = TOOL_AUTH_SCOPES.get(tool_name, "public")
        if auth_scope is not None and tool_auth_scope != auth_scope:
            continue

        # Deprecated filter
        if tool_name in TOOL_DEPRECATED and not include_deprecated:
            continue

        # Category filter (explicit TOOL_CATEGORIES dict)
        if category is not None and category not in TOOL_CATEGORIES.get(tool_name, []):
            continue

        results.append(_dict_to_manifest(tool_name, tool_auth_scope))

    return results


async def get_server_manifest(server_name: str) -> MCPServerManifest | None:
    """Get the full manifest bundle for a given server."""
    tools: list[MCPToolManifest] = []
    auth_scopes: set[str] = set()

    for tool_name, _entry in _TOOL_BY_NAME.items():
        if _extract_server(tool_name) != server_name:
            continue
        scope = TOOL_AUTH_SCOPES.get(tool_name, "public")
        auth_scopes.add(scope)
        tools.append(_dict_to_manifest(tool_name, scope))

    if not tools:
        log.warning("No tools found for server: %s", server_name)
        return None

    return MCPServerManifest(
        name=server_name,
        transport="sse",
        endpoint="/mcp/sse",
        tools=tools,
        auth_scopes=sorted(auth_scopes),
    )
