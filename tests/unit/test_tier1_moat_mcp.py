"""M3 moat TIER 1 - tests for new MCP tools (analytics_query, mcp_discover, status_check).

These tests verify the tool catalog, versioning, and dispatch - all without
needing a live database or external service. They test the contract.
"""
from __future__ import annotations

from app.mcp.server import (
    MCP_PROTOCOL_VERSION,
    MCP_SERVER_VERSION,
    TOOL_CATALOG,
    TOOL_DEPRECATED,
    TOOL_SUCCESSORS,
    TOOL_VERSIONS,
)


# ── Catalog completeness ─────────────────────────────────────────
def test_tool_catalog_has_minimum_count():
    """MCP catalog must have at least 8 original tools + 3 new TIER 1 tools = 11+."""
    assert len(TOOL_CATALOG) >= 11, f"only {len(TOOL_CATALOG)} tools, expected 11+"


def test_new_tier1_tools_present():
    """The 3 new TIER 1 moat tools must be in the catalog."""
    names = {t["name"] for t in TOOL_CATALOG}
    assert "analytics_query" in names, "analytics_query tool missing"
    assert "mcp_discover" in names, "mcp_discover tool missing"
    assert "status_check" in names, "status_check tool missing"


def test_all_tools_have_input_schema():
    """Every tool must declare an inputSchema (JSON Schema 2020-12)."""
    for tool in TOOL_CATALOG:
        assert "inputSchema" in tool, f"{tool.get('name')} missing inputSchema"
        assert tool["inputSchema"].get("type") == "object", (
            f"{tool['name']} inputSchema.type must be 'object'"
        )


def test_all_tools_have_descriptions():
    """Every tool must have a human-readable description."""
    for tool in TOOL_CATALOG:
        assert tool.get("description"), f"{tool.get('name')} missing description"
        assert len(tool["description"]) > 20, f"{tool['name']} description too short"


def test_all_tools_have_versions():
    """Every tool must have a semantic version in TOOL_VERSIONS."""
    catalog_names = {t["name"] for t in TOOL_CATALOG}
    versioned_names = set(TOOL_VERSIONS.keys())
    missing = catalog_names - versioned_names
    assert not missing, f"tools missing versions: {missing}"


def test_version_format_is_semver():
    """All tool versions must be MAJOR.MINOR.PATCH."""
    import re
    semver_re = re.compile(r"^\d+\.\d+\.\d+$")
    for name, ver in TOOL_VERSIONS.items():
        assert semver_re.match(ver), f"{name} version '{ver}' is not semver"


def test_bayesian_reputation_version_bumped():
    """M3 - deployer reputation must be v2.x (Bayesian upgrade)."""
    ver = TOOL_VERSIONS.get("get_deployer_reputation", "")
    assert ver.startswith("2."), f"get_deployer_reputation should be v2.x (Bayesian), got {ver}"


def test_generate_report_version_bumped():
    """M3 - generate_report must be v2.x (RAG-grounded)."""
    ver = TOOL_VERSIONS.get("generate_report", "")
    assert ver.startswith("2."), f"generate_report should be v2.x (RAG-grounded), got {ver}"


# ── Deprecation registry ─────────────────────────────────────────
def test_deprecated_set_is_valid():
    """Every deprecated tool must also be in TOOL_CATALOG."""
    for name in TOOL_DEPRECATED:
        catalog_names = {t["name"] for t in TOOL_CATALOG}
        assert name in catalog_names, f"deprecated tool {name} not in catalog"


def test_successor_targets_exist():
    """Every successor must point to a tool that exists in the catalog."""
    catalog_names = {t["name"] for t in TOOL_CATALOG}
    for old, new in TOOL_SUCCESSORS.items():
        assert new in catalog_names, f"successor '{new}' for '{old}' not in catalog"


# ── Server metadata ──────────────────────────────────────────────
def test_server_version_is_set():
    """Server version must be a non-empty semantic version."""
    import re
    assert re.match(r"^\d+\.\d+\.\d+$", MCP_SERVER_VERSION), (
        f"MCP_SERVER_VERSION '{MCP_SERVER_VERSION}' is not semver"
    )


def test_protocol_version_is_set():
    """MCP protocol version must be set (2024-11-05 is the current spec)."""
    assert MCP_PROTOCOL_VERSION
    assert len(MCP_PROTOCOL_VERSION) > 5


# ── DuckDB max_rows safety ────────────────────────────────────────
def test_duckdb_query_supports_max_rows():
    """DuckDB query() must support max_rows to prevent MCP API DoS."""
    import inspect

    from app.core.duckdb_analytics import DuckDBAnalytics
    sig = inspect.signature(DuckDBAnalytics.query)
    assert "max_rows" in sig.parameters, "DuckDBAnalytics.query must support max_rows parameter"


def test_duckdb_max_rows_caps_results():
    """When max_rows is set, fetchmany is used (not fetchall)."""
    from app.core.duckdb_analytics import DuckDBAnalytics
    d = DuckDBAnalytics()
    # Insert 5 rows
    d._conn.execute("CREATE TABLE IF NOT EXISTS _test_max_rows AS SELECT 1 AS n")
    d._conn.execute("INSERT INTO _test_max_rows VALUES (1),(2),(3),(4),(5)")
    rows = d.query("SELECT * FROM _test_max_rows ORDER BY n", max_rows=2)
    assert len(rows) == 2, f"max_rows=2 should return 2 rows, got {len(rows)}"
    d._conn.execute("DROP TABLE _test_max_rows")


# ── Tool name format ─────────────────────────────────────────────
def test_tool_names_are_kebab_case():
    """Tool names should be lowercase with underscores (per MCP convention)."""
    import re
    name_re = re.compile(r"^[a-z][a-z0-9_]*$")
    for tool in TOOL_CATALOG:
        name = tool["name"]
        assert name_re.match(name), f"tool name '{name}' is not kebab/snake_case"
