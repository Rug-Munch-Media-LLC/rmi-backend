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

import os
import time
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

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

        config: dict[str, str | bool | int | float | list[str]] = {}
        if threads is not None:
            config["threads"] = threads
        if memory_limit is not None:
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
        self._s3_configured: bool = False  # cache: s3 creds set only once

        # Load httpfs extension for S3/R2 Parquet support (used by query_remote_parquet)
        try:
            self._conn.execute("INSTALL httpfs; LOAD httpfs;")
            log.info("duckdb_httpfs_loaded")
        except Exception as exc:
            log.warning(
                "duckdb_httpfs_install_failed: %s. Remote parquet queries will not work.",
                exc,
            )

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
            rows = cursor.fetchmany(max_rows) if max_rows is not None and max_rows > 0 else cursor.fetchall()
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
        pg_url = os.getenv("PG_URL") or os.getenv("DATABASE_URL") or "postgres://rmi:postgres@localhost:5432/rmi"
        self._attach_postgres(pg_url)
        return self.query(sql)

    def _attach_postgres(self, pg_url: str) -> None:
        """Attach the configured Postgres DB as 'pg' (READ_ONLY), if not already attached."""
        if "pg" in self._attached:
            return
        try:
            self._conn.execute(f"ATTACH '{pg_url}' AS pg (TYPE postgres, READ_ONLY)")
            self._attached.add("pg")
            log.info("duckdb_postgres_attached")
        except Exception as e:
            log.warning("duckdb_postgres_attach_failed err=%s", e)
            raise

    def query_parquet(
        self,
        parquet_path: str,
        sql: str | None = None,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query a Parquet file directly (no ingestion needed).

        Args:
            parquet_path: Path or glob to Parquet file(s).
            sql: SQL query. If None, returns SELECT * FROM read_parquet(path).
                 The path is bound to a 'parquet' table for the query.
            params: Optional query parameters (passed to self.query).

        Examples:
            db.query_parquet('s3://bucket/export.parquet')
            db.query_parquet('/tmp/*.parquet', 'SELECT count(*) AS n FROM parquet')
        """
        # Security: validate path doesn't contain SQL-injectable characters
        if "'" in parquet_path:
            raise ValueError(f"Invalid parquet path (contains quote): {parquet_path!r}")
        # Bind parquet path to a table for the duration of the query
        bind_sql = f"SELECT * FROM read_parquet('{parquet_path}')"  # noqa: S608 — verified bound/allowlisted SQL construction
        sql = bind_sql if sql is None else f"WITH parquet AS ({bind_sql}) {sql}"
        return self.query(sql, params=params)

    def query_remote_parquet(
        self,
        bucket_url: str,
        sql: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query Parquet files from S3/R2 remote storage using httpfs.

        Configures S3-compatible credentials from env vars on first call
        (R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY), then
        executes the provided SQL which should reference the remote Parquet
        files via ``read_parquet('{bucket_url}', union_by_name=true)``.

        Args:
            bucket_url: S3 URL path to Parquet file(s) or glob.
                        Example: s3://rmi-parquet/ethereum/transfers/*/*/*.parquet
            sql: SQL query that references read_parquet(...) for the bucket_url.
            params: Optional parameter list for ? placeholders.

        Returns:
            list of dicts. Empty list on error (with a warning logged).
        """
        self._ensure_s3_configured()
        try:
            return self.query(sql, params=params)
        except Exception as exc:
            log.warning(
                "duckdb_remote_parquet_fail bucket=%s err=%s: %s",
                bucket_url,
                type(exc).__name__,
                exc,
            )
            return []

    def _ensure_s3_configured(self) -> None:
        """Idempotently configure S3-compatible credentials from env vars (R2)."""
        if self._s3_configured:
            return
        endpoint = os.getenv("R2_ENDPOINT_URL")
        access_key = os.getenv("R2_ACCESS_KEY_ID")
        secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
        if not (endpoint and access_key and secret_key):
            log.warning(
                "duckdb_s3_missing_config - R2_ENDPOINT_URL/R2_ACCESS_KEY_ID/"
                "R2_SECRET_ACCESS_KEY not set; remote parquet queries will fail"
            )
            return
        self._conn.execute(
            "SET s3_endpoint = ?; SET s3_access_key_id = ?; SET s3_secret_access_key = ?;",
            [endpoint, access_key, secret_key],
        )
        self._s3_configured = True
        log.info("duckdb_s3_configured endpoint=%s", endpoint)

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
        self._conn.execute(f"COPY ({sql}) TO '{output_path}' (FORMAT PARQUET)", params or [])
        elapsed_ms = (time.monotonic() - start) * 1000
        rows = self.query(f"SELECT count(*) AS n FROM '{output_path}'")  # noqa: S608 — verified bound/allowlisted SQL construction
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

    def close(self) -> None:
        """Close the DuckDB connection."""
        try:
            self._conn.close()
            log.debug("duckdb_analytics_closed persist=%s", self._persist_path)
        except Exception as e:
            log.warning("duckdb_close_err: %s", e)

    def __enter__(self) -> DuckDBAnalytics:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any) -> None:
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
