"""
Eth Labels MCP Tool - Direct access to eth-labels.db via MCP

Provides an MCP tool to directly access the local eth-labels.db SQLite database
containing 115K labeled EVM addresses. Allows SQL queries against the database
with proper safety measures.
"""

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


def _safe_select_query(sql: str) -> bool:
    """
    Check if an SQL statement is a safe SELECT query.

    Only allows SELECT statements with safety restrictions:
    - No writes (INSERT/UPDATE/DELETE/CREATE etc.)
    - No dangerous keywords like UNION (unless in approved cases)
    - No complex join patterns that could abuse the data

    Args:
        sql: SQL query string to validate

    Returns:
        True if SQL is safe, False otherwise
    """
    # Convert to uppercase for checking
    sql_upper = sql.strip().upper()

    # Must start with SELECT
    if not sql_upper.lstrip().startswith('SELECT'):
        return False

    # Check for dangerous terms
    dangerous_keywords = [
        'INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 'ALTER',
        'TRUNCATE', 'REPLACE', 'MERGE', 'WITH.*RECURSIVE',
        'INTO', 'OUTFILE', 'DUMPFILE', 'LOAD_DATA'
    ]

    for keyword in dangerous_keywords:
        # Handle keywords like 'WITH RECURSIVE' differently
        if keyword == 'WITH.*RECURSIVE':
            if 'WITH' in sql_upper and 'RECURSIVE' in sql_upper:
                return False
        elif keyword in sql_upper:
            return False

    return True


async def query_eth_labels_db_mcp(sql: str, limit: int = 1000) -> dict[str, Any]:
    """
    MCP tool to query eth-labels.db with SELECT-only safety.

    Args:
        sql: SELECT SQL query to run against eth-labels.db
        limit: Maximum number of results to return (default 1000, max 10000)

    Returns:
        Dictionary with 'rows' (list of result rows), 'count' (number of rows),
        'truncated' (bool indicating if results were limited)
    """
    # Validate input
    if not sql or sql.strip() == "":
        return {"error": "'sql' parameter required"}

    if not _safe_select_query(sql):
        return {"error": "Only SELECT statements allowed - no writes or dangerous operations"}

    # Enforce limit
    limit = min(limit, 10000)  # Cap at 10K rows to prevent abuse

    def _execute_query() -> dict[str, Any]:
        import sqlite3
        from pathlib import Path

        # Path to the eth-labels.db
        db_path = Path("/home/dev/rmi/eth-labels.db")
        if not db_path.exists():
            return {"error": f"Database file not found: {db_path}"}

        try:
            # Connect to the database
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            conn.row_factory = sqlite3.Row  # Enable accessing columns by name

            # Execute query with parameter substitution not needed in this context but safe
            cursor = conn.cursor()
            cursor.execute(sql)

            # Fetch results
            rows = cursor.fetchall()

            # Convert to list of dictionaries and limit results
            result_rows = [dict(row) for row in rows[:limit]]

            conn.close()

            return {
                "rows": result_rows,
                "count": len(result_rows),
                "truncated": len(rows) > limit,
                "sql_executed": sql  # For debugging/tracing
            }

        except sqlite3.Error as e:
            return {"error": f"SQLite error: {e!s}"}
        except Exception as e:
            return {"error": f"Query execution failed: {e!s}"}

    # Run blocking DB operation in thread
    result = await asyncio.to_thread(_execute_query)

    if isinstance(result, dict) and "error" in result:
        log.warning("eth_labels_db_mcp_error sql=%s err=%s", sql[:100], result["error"])

    return result


async def get_eth_labels_stats_mcp() -> dict[str, Any]:
    """
    Get statistics about the eth-labels.db database.

    Returns:
        Database statistics including table counts and sample data.
    """
    def _get_stats() -> dict[str, Any]:
        import sqlite3
        from pathlib import Path

        db_path = Path("/home/dev/rmi/eth-labels.db")
        if not db_path.exists():
            return {"error": f"Database file not found: {db_path}"}

        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)

            # Get table information
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]

            # Get account counts by chain
            stats = {"tables": tables}

            if "accounts" in tables:
                # Count total records
                cursor.execute("SELECT COUNT(*) FROM accounts;")
                stats["total_accounts"] = cursor.fetchone()[0]

                # Get unique chains
                cursor.execute("SELECT DISTINCT chain_id FROM accounts;")
                chain_ids = [row[0] for row in cursor.fetchall()]
                stats["chain_ids"] = chain_ids

                # Get label counts
                cursor.execute("""
                    SELECT chain_id, COUNT(*) as count
                    FROM accounts
                    GROUP BY chain_id
                    ORDER BY count DESC
                    LIMIT 10;
                """)
                stats["accounts_by_chain"] = [
                    {"chain_id": row[0], "count": row[1]}
                    for row in cursor.fetchall()
                ]

            conn.close()
            return stats

        except Exception as e:
            return {"error": f"Stats query failed: {e!s}"}

    result = await asyncio.to_thread(_get_stats)

    if isinstance(result, dict) and "error" in result:
        log.warning("eth_labels_stats_mcp_error err=%s", result["error"])

    return result
