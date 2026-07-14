"""DuckDB Embedded Analytics - RMI v5 §T13 (P2).

Per RMIV5: small analytics queries (<1 GB) don't need ClickHouse.
DuckDB is in-process, 10x faster, zero infrastructure. Drop-in for
ad-hoc queries on:
  - Exported Parquet/CSV from MinIO (S3-compatible)
  - Catalog CSV exports
  - Cross-source joins (Postgres + Parquet)

Why DuckDB:
  - No server to operate (in-process, like SQLite but columnar)
  - Native Parquet/CSV/JSON readers - no ETL needed
  - Postgres wire protocol compatible (could expose as service later)
  - Vectorized execution, ~10x faster than ClickHouse for small queries
  - Can ATTACH Postgres as a read source for cross-DB joins

Architecture:
  - DuckDBAnalytics(): main entry point, in-memory by default
  - query(sql, params): run arbitrary SQL, return rows as list[dict]
  - query_postgres(sql): attach Postgres as READ_ONLY, run join query
  - query_parquet(path, sql): query exported Parquet files
  - register_dataframe(name, df): register a pandas DataFrame as a table
  - export_to_parquet(sql, path): export query results to Parquet
  - close(): close the connection

Thread safety: each DuckDBAnalytics instance is owned by one caller.
For concurrent use, create separate instances or use a connection pool.

Per RMIV5 v4.0 §T31 (perf gap): ClickHouse has 2 GB memory cap +
network overhead. DuckDB handles "give me counts by chain" in <10ms
with zero setup.
"""

from __future__ import annotations

from app.core.logging import get_logger
import os
import time
from pathlib import Path
from typing import Any

log = get_logger(__name__)


class DuckDBAnalytics:
    """Embedded DuckDB analytics engine.

    Default: in-memory database (fastest, no persistence).
    For persistent storage: DuckDBAnalytics(persist_path='/var/lib/duckdb/rmi.db').
    """

    def __init__(
        self,
        persist_path: str | None = None,
        threads: int | None = None,
        memory_limit: str | None = None,
    ) -> None:
        """Initialize DuckDB connection.

        Args:
            persist_path: If set, use a file-backed DB at this path.
                          If None, use in-memory (lost on close).
            threads: Number of CPU threads. None = DuckDB default (cores).
            memory_limit: e.g. '2GB'. None = no limit.
        """
        import duckdb  # imported lazily so import cost only on first use

        config = {}
        if threads:
            config["threads"] = threads
        if memory_limit:
            config["memory_limit"] = memory_limit

        if persist_path:
            Path(persist_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(persist_path, config=config)
            log.info("duckdb_analytics_init persist=%s config=%s", persist_path, config)
        else:
            self._conn = duckdb.connect(":memory:", config=config)
            log.debug("duckdb_analytics_init in-memory config=%s", config)

        self._persist_path = persist_path
        self._attached: set[str] = set()  # track attached DBs to avoid double-attach

    def query(
        self,
        sql: str,
        params: list[Any] | None = None,
        max_rows: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a SQL query and return rows as list of dicts.

        Args:
            sql: SQL query. Use ? placeholders for params.
            params: List of parameter values for ? placeholders.
            max_rows: Optional cap on returned rows (for MCP/API safety).

        Returns:
            list of dicts, one per row. Empty list if no results.

        Examples:
            r = db.query("SELECT 1 AS n")
            # [{"n": 1}]

            r = db.query("SELECT count(*) AS c FROM tokens WHERE chain = ?", ["ethereum"])
            # [{"c": 1234}]
        """
        start = time.monotonic()
        try:
            cursor = self._conn.execute(sql, params or [])
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = (
                cursor.fetchmany(max_rows)
                if max_rows is not None and max_rows > 0
                else cursor.fetchall()
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            log.info(
                "duckdb_query rows=%d columns=%d took_ms=%.2f",
                len(rows),
                len(columns),
                elapsed_ms,
            )
            return [dict(zip(columns, row, strict=False)) for row in rows]
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            log.error("duckdb_query_fail took_ms=%.2f err=%s: %s", elapsed_ms, type(e).__name__, e)
            raise

    def query_postgres(self, sql: str) -> list[dict[str, Any]]:
        """Run SQL that joins/reads from Postgres.

        Attaches the configured Postgres DB as 'pg' (READ_ONLY) so the
        query can reference pg.table_name. Uses the env var PG_URL or
        DATABASE_URL.

        Example:
            db.query_postgres('''
                SELECT t.chain, count(*) AS n
                FROM pg.tokens t
                WHERE t.deployed_at > ?
                GROUP BY t.chain
            ''')

        Args:
            sql: SQL with optional pg.<table> references.

        Returns:
            list of dicts.
        """
        pg_url = (
            os.getenv("PG_URL")
            or os.getenv("DATABASE_URL")
            or "postgres://rmi:postgres@localhost:5432/rmi"
        )
        self._attach_postgres(pg_url)
        return self.query(sql)

    def query_parquet(self, parquet_path: str, sql: str | None = None) -> list[dict[str, Any]]:
        """Query a Parquet file directly (no ingestion needed).

        Args:
            parquet_path: Path or glob to Parquet file(s).
            sql: SQL query. If None, returns SELECT * FROM read_parquet(path).
                 The path is bound to a 'parquet' table for the query.

        Examples:
            db.query_parquet('s3://bucket/export.parquet')
            db.query_parquet('/tmp/*.parquet', 'SELECT count(*) AS n FROM parquet')
        """
        # Security: validate path doesn't contain SQL-injectable characters
        if "'" in parquet_path:
            raise ValueError(f"Invalid parquet path (contains quote): {parquet_path!r}")
        # Bind parquet path to a table for the duration of the query
        bind_sql = f"SELECT * FROM read_parquet('{parquet_path}')"
        if sql is None:  # noqa: SIM108
            sql = bind_sql
        else:
            # Inject the parquet binding as a CTE the user can reference
            sql = f"WITH parquet AS ({bind_sql}) {sql}"
        return self.query(sql)

    def register_dataframe(self, name: str, df: Any) -> None:
        """Register a pandas/polars DataFrame as a queryable table.

        Args:
            name: Table name to use in queries.
            df: pandas.DataFrame or polars.DataFrame.
        """
        self._conn.register(name, df)
        log.info("duckdb_register_df name=%s rows=%d", name, len(df))

    def export_to_parquet(self, sql: str, output_path: str, params: list[Any] | None = None) -> int:
        """Run a query and export results to Parquet.

        Args:
            sql: SQL query (results become the Parquet content).
            output_path: Where to write the Parquet file.
            params: Optional parameter list.

        Returns:
            Number of rows exported.
        """
        # Security: validate path doesn't contain SQL-injectable characters
        if "'" in output_path:
            raise ValueError(f"Invalid output path (contains quote): {output_path!r}")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        # Use COPY (SELECT ... ) TO 'file.parquet' (FORMAT PARQUET) for direct export
        start = time.monotonic()
        self._conn.execute(f"COPY ({sql}) TO ? (FORMAT PARQUET)", [output_path, *(params or [])])
        elapsed_ms = (time.monotonic() - start) * 1000
        # Count rows - validated path, safe for string interpolation
        rows = self.query(f"SELECT count(*) AS n FROM '{output_path}'")
        n = rows[0]["n"] if rows else 0
        log.info(
            "duckdb_export_parquet rows=%d path=%s took_ms=%.2f",
            n,
            output_path,
            elapsed_ms,
        )
        return int(n)

    def table_exists(self, table_name: str) -> bool:
        """Check if a table is registered in this connection."""
        rows = self.query(
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_name = ?",
            [table_name],
        )
        return bool(rows and rows[0]["n"] > 0)

    def list_tables(self) -> list[str]:
        """List all registered tables."""
        rows = self.query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name"
        )
        return [r["table_name"] for r in rows]

    def _attach_postgres(self, pg_url: str) -> None:
        """Attach a Postgres DB as READ_ONLY under the alias 'pg'.

        Idempotent: skips if already attached.
        """
        if "pg" in self._attached:
            return
        # DuckDB's ATTACH syntax for Postgres: ATTACH 'postgres://...' AS pg (READ_ONLY)
        # Use the SQL escaping: escape single quotes in URL by doubling them
        escaped_url = pg_url.replace("'", "''")
        self._conn.execute(f"ATTACH '{escaped_url}' AS pg (READ_ONLY)")
        self._attached.add("pg")
        log.info("duckdb_attached_postgres alias=pg")

    def close(self) -> None:
        """Close the DuckDB connection."""
        try:
            self._conn.close()
            log.debug("duckdb_analytics_closed persist=%s", self._persist_path)
        except Exception as e:
            log.warning("duckdb_close_err: %s", e)

    def __enter__(self) -> DuckDBAnalytics:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# Convenience factory
_default_instance: DuckDBAnalytics | None = None


def get_default_analytics() -> DuckDBAnalytics:
    """Get a process-wide DuckDBAnalytics instance.

    Use this for one-off analytics queries that don't need their own
    persistent DB. The instance is reused across calls.
    """
    global _default_instance
    if _default_instance is None:
        _default_instance = DuckDBAnalytics()
    return _default_instance
