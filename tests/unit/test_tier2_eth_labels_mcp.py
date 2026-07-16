"""Tests for Tier 2 MCP tools added to RMI.

This file tests the eth-labels MCP tools implemented for Tier 2:
- eth_labels_query: Direct access to eth-labels.db SQLite
- eth_labels_stats: Statistics about eth-labels.db dataset
"""

from unittest.mock import patch

import pytest

from app.mcp.server import call_tool


@pytest.mark.asyncio
async def test_eth_labels_query_tool_exists():
    """Test that eth_labels_query tool is registered in the server."""
    import app.mcp.server

    # Check if it's in the catalog
    tool_names = [tool["name"] for tool in app.mcp.server.TOOL_CATALOG]
    assert "eth_labels_query" in tool_names
    print("✓ eth_labels_query found in tool catalog")


@pytest.mark.asyncio
async def test_eth_labels_stats_tool_exists():
    """Test that eth_labels_stats tool is registered in the server."""
    import app.mcp.server

    tool_names = [tool["name"] for tool in app.mcp.server.TOOL_CATALOG]
    assert "eth_labels_stats" in tool_names
    print("✓ eth_labels_stats found in tool catalog")


@pytest.mark.asyncio
async def test_eth_labels_query_version_tracked():
    """Test that eth_labels_query tool has version tracking."""
    from app.mcp.server import TOOL_VERSIONS

    assert "eth_labels_query" in TOOL_VERSIONS
    version = TOOL_VERSIONS["eth_labels_query"]
    assert version == "1.0.0"  # Version from Tier 2
    print(f"✓ eth_labels_query version: {version}")


@pytest.mark.asyncio
async def test_eth_labels_stats_version_tracked():
    """Test that eth_labels_stats tool has version tracking."""
    from app.mcp.server import TOOL_VERSIONS

    assert "eth_labels_stats" in TOOL_VERSIONS
    version = TOOL_VERSIONS["eth_labels_stats"]
    assert version == "1.0.0"  # Version from Tier 2
    print(f"✓ eth_labels_stats version: {version}")


@pytest.mark.asyncio
@patch("app.mcp.tools.eth_labels_tool.query_eth_labels_db_mcp")
async def test_eth_labels_query_calls_underlying_function(mock_query):
    """Test that eth_labels_query tool calls our implementation."""
    # Mock the response
    mock_response = {"rows": [{"id": 1, "address": "0x123"}], "count": 1}
    mock_query.return_value = mock_response

    result = await call_tool("eth_labels_query", {"sql": "SELECT * FROM accounts LIMIT 10"})

    # Verify the tool processed the call
    mock_query.assert_called_once_with("SELECT * FROM accounts LIMIT 10", 1000)
    assert "result" in result
    assert result["result"]["rows"][0]["id"] == 1
    print("✓ eth_labels_query properly calls underlying function")


@pytest.mark.asyncio
@patch("app.mcp.tools.eth_labels_tool.get_eth_labels_stats_mcp")
async def test_eth_labels_stats_calls_underlying_function(mock_stats):
    """Test that eth_labels_stats calls our implementation."""
    mock_response = {"total_accounts": 106000, "tables": ["accounts"]}
    mock_stats.return_value = mock_response

    result = await call_tool("eth_labels_stats", {})

    mock_stats.assert_called_once()
    assert "result" in result
    assert result["result"]["total_accounts"] == 106000
    print("✓ eth_labels_stats properly calls underlying function")


@pytest.mark.asyncio
async def test_eth_labels_query_requires_sql_parameter():
    """Test that eth_labels_query properly validates input."""
    result = await call_tool("eth_labels_query", {})

    assert "error" in result
    assert "sql parameter required" in result["error"]
    print("✓ eth_labels_query validates required parameters")


@pytest.mark.asyncio
async def test_eth_labels_query_blocks_non_select_statements():
    """Test that eth_labels_query rejects non-SELECT queries."""
    result = await call_tool(
        "eth_labels_query", {"sql": "UPDATE accounts SET label='bad' WHERE id=1"}
    )

    assert "error" in result
    assert "only SELECT queries allowed" in result["error"]
    print("✓ eth_labels_query blocks non-SELECT queries")


if __name__ == "__main__":
    """Direct test runner."""
    import asyncio

    async def run_tests():
        print("Running Tier 2 eth-labels MCP tool tests...")

        await test_eth_labels_query_tool_exists()
        await test_eth_labels_stats_tool_exists()
        await test_eth_labels_query_version_tracked()
        await test_eth_labels_stats_version_tracked()
        await test_eth_labels_query_requires_sql_parameter()
        await test_eth_labels_query_blocks_non_select_statements()

        print("\nAll Tier 2 eth-labels MCP tool tests passed! 🎉")

    asyncio.run(run_tests())
