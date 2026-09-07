"""Security tests: MCP analytics_query tool requires a valid API key.

Audit 2026-09-06: the analytics_query MCP tool executed arbitrary read-only
SQL with no authentication. These tests assert that calls WITHOUT a valid
API key are rejected BEFORE any query execution, and that the catalog
schema marks api_key as required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_analytics_query_rejected_without_api_key():
    """Missing api_key → error response, DuckDB never instantiated."""
    from app.mcp import server as mcp_server

    with patch("app.core.duckdb_analytics.DuckDBAnalytics") as duckdb_cls:
        result = await mcp_server.call_tool("analytics_query", {"sql": "SELECT 1"})

    assert "error" in result
    assert "unauthorized" in result["error"]
    duckdb_cls.assert_not_called()


@pytest.mark.asyncio
async def test_analytics_query_rejected_with_invalid_api_key():
    """Invalid/revoked api_key → error response, no query execution."""
    from app.mcp import server as mcp_server

    with (
        patch("app.core.auth._is_valid_admin_api_key", new=AsyncMock(return_value=False)),
        patch("app.core.duckdb_analytics.DuckDBAnalytics") as duckdb_cls,
    ):
        result = await mcp_server.call_tool("analytics_query", {"sql": "SELECT 1", "api_key": "sk-fake-revoked-key"})

    assert "error" in result
    assert "unauthorized" in result["error"]
    duckdb_cls.assert_not_called()


@pytest.mark.asyncio
async def test_analytics_query_allowed_with_valid_api_key():
    """Valid api_key → query executes and returns rows."""
    from app.mcp import server as mcp_server

    fake_rows = [{"n": 1}]
    with (
        patch("app.core.auth._is_valid_admin_api_key", new=AsyncMock(return_value=True)),
        patch("app.core.duckdb_analytics.DuckDBAnalytics") as duckdb_cls,
    ):
        duckdb_cls.return_value.query.return_value = fake_rows
        result = await mcp_server.call_tool("analytics_query", {"sql": "SELECT 1 AS n", "api_key": "sk-valid-key"})

    assert "error" not in result
    assert result["result"]["rows"] == fake_rows
    assert result["engine"] == "duckdb"


def test_catalog_schema_requires_api_key():
    """The published tool schema must mark api_key as required."""
    from app.mcp._catalog import TOOL_CATALOG

    entry = next(t for t in TOOL_CATALOG if t["name"] == "analytics_query")
    assert "api_key" in entry["inputSchema"]["required"]
    assert "api_key" in entry["inputSchema"]["properties"]
