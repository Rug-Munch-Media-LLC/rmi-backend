"""Tests for app/core/duckdb_analytics.py (T13 - RMIV5).

Per RMIV5 §T13: DuckDB is an in-process analytics engine for queries
too small for ClickHouse but still needing columnar speed. These tests
verify:
  - Basic SELECT returns list[dict]
  - Parameterized queries (? placeholders)
  - Parquet round-trip (export + query)
  - DataFrame registration
  - Context manager cleanup
  - Postgres attach (mocked)
  - Error handling on bad SQL
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.core.duckdb_analytics import DuckDBAnalytics, get_default_analytics


@pytest.fixture
def db():
    """Fresh in-memory DuckDB per test."""
    d = DuckDBAnalytics()
    yield d
    d.close()


class TestBasicQuery:
    """Core SELECT queries."""

    def test_simple_select(self, db) -> None:
        r = db.query("SELECT 1 AS n, 'hello' AS msg")
        assert r == [{"n": 1, "msg": "hello"}]

    def test_empty_result(self, db) -> None:
        r = db.query("SELECT 1 AS n WHERE 1 = 0")
        assert r == []

    def test_multiple_rows(self, db) -> None:
        r = db.query("SELECT i FROM range(0, 5) t(i) ORDER BY i")
        assert r == [{"i": 0}, {"i": 1}, {"i": 2}, {"i": 3}, {"i": 4}]

    def test_null_values(self, db) -> None:
        r = db.query("SELECT NULL AS nothing, 1 AS one")
        assert r == [{"nothing": None, "one": 1}]


class TestParameterizedQuery:
    """? placeholder binding."""

    def test_string_param(self, db) -> None:
        r = db.query("SELECT ? AS name", ["alice"])
        assert r == [{"name": "alice"}]

    def test_int_param(self, db) -> None:
        r = db.query("SELECT ? + 10 AS answer", [32])
        assert r == [{"answer": 42}]

    def test_multiple_params(self, db) -> None:
        r = db.query(
            "SELECT ? AS a, ? AS b, ? AS c",
            ["x", 1, True],
        )
        assert r == [{"a": "x", "b": 1, "c": True}]

    def test_params_in_where_clause(self, db) -> None:
        r = db.query(
            "SELECT i FROM range(0, 10) t(i) WHERE i > ? AND i < ?",
            [3, 7],
        )
        assert [row["i"] for row in r] == [4, 5, 6]

    def test_no_params_works(self, db) -> None:
        r = db.query("SELECT 1 AS n")
        assert r == [{"n": 1}]


class TestParquetRoundTrip:
    """Export to Parquet + query back."""

    def test_export_and_query(self, db) -> None:
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            path = f.name
        try:
            n = db.export_to_parquet(
                "SELECT i, i*2 AS doubled, i*i AS squared FROM range(0, 50) t(i)",
                path,
            )
            assert n == 50

            # Query back
            r = db.query(f"SELECT count(*) AS n FROM '{path}'")
            assert r == [{"n": 50}]

            # Aggregation
            r = db.query(f"SELECT count(*) AS n, sum(doubled) AS total FROM '{path}'")
            assert r[0]["n"] == 50
            assert r[0]["total"] == 2 * sum(range(50))
        finally:
            os.unlink(path)

    def test_query_with_explicit_sql(self, db) -> None:
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            path = f.name
        try:
            db.export_to_parquet(
                "SELECT chain, count(*) AS n FROM (VALUES ('eth'), ('eth'), ('sol')) t(chain) GROUP BY chain",
                path,
            )
            # Use query_parquet with explicit SQL
            r = db.query_parquet(path, "SELECT chain, n FROM parquet ORDER BY chain")
            assert r == [{"chain": "eth", "n": 2}, {"chain": "sol", "n": 1}]
        finally:
            os.unlink(path)

    def test_export_creates_parent_dirs(self, db) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "a", "b", "c", "out.parquet")
            n = db.export_to_parquet("SELECT 1 AS n", nested)
            assert n == 1
            assert os.path.exists(nested)


class TestDataFrameRegistration:
    """Register pandas DataFrames as queryable tables."""

    def test_register_and_query(self, db) -> None:
        import pandas as pd

        df = pd.DataFrame({"name": ["alice", "bob"], "val": [10, 20]})
        db.register_dataframe("users", df)
        r = db.query("SELECT name, val FROM users ORDER BY val DESC")
        assert r == [{"name": "bob", "val": 20}, {"name": "alice", "val": 10}]

    def test_register_with_aggregation(self, db) -> None:
        import pandas as pd

        df = pd.DataFrame({"chain": ["eth", "eth", "sol"], "amount": [100, 200, 50]})
        db.register_dataframe("txs", df)
        r = db.query("SELECT chain, sum(amount) AS total FROM txs GROUP BY chain ORDER BY chain")
        assert r == [{"chain": "eth", "total": 300}, {"chain": "sol", "total": 50}]


class TestContextManager:
    """with-statement lifecycle."""

    def test_context_manager(self) -> None:
        with DuckDBAnalytics() as d:
            r = d.query("SELECT 1 AS x")
            assert r == [{"x": 1}]
        # After exit, connection should be closed (subsequent ops fail)
        # We just verify the with-statement works cleanly.

    def test_explicit_close(self) -> None:
        d = DuckDBAnalytics()
        d.query("SELECT 1")
        d.close()
        # Subsequent ops should fail (connection closed)
        with pytest.raises(Exception):
            d.query("SELECT 1")


class TestPersistence:
    """File-backed DB mode."""

    def test_persistent_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.db")

            # First connection: create data
            d1 = DuckDBAnalytics(persist_path=path)
            d1.query("CREATE TABLE foo (n INTEGER)")
            d1.query("INSERT INTO foo VALUES (1), (2), (3)")
            d1.close()

            # Second connection: verify data persists
            d2 = DuckDBAnalytics(persist_path=path)
            r = d2.query("SELECT count(*) AS n FROM foo")
            assert r == [{"n": 3}]
            d2.close()


class TestTableInfo:
    """table_exists + list_tables."""

    def test_table_exists_true(self, db) -> None:
        db.query("CREATE TABLE foo (n INTEGER)")
        assert db.table_exists("foo") is True

    def test_table_exists_false(self, db) -> None:
        assert db.table_exists("nonexistent") is False

    def test_list_tables(self, db) -> None:
        db.query("CREATE TABLE a (x INTEGER)")
        db.query("CREATE TABLE b (y INTEGER)")
        db.query("CREATE TABLE c (z INTEGER)")
        tables = db.list_tables()
        assert set(tables) >= {"a", "b", "c"}


class TestErrorHandling:
    """Bad SQL should raise, not silently return empty."""

    def test_bad_sql_raises(self, db) -> None:
        with pytest.raises(Exception):
            db.query("SELECT * FROM nonexistent_table")

    def test_syntax_error_raises(self, db) -> None:
        with pytest.raises(Exception):
            db.query("THIS IS NOT VALID SQL")


class TestDefaultAnalytics:
    """Process-wide singleton."""

    def test_get_default_returns_instance(self) -> None:
        d = get_default_analytics()
        assert isinstance(d, DuckDBAnalytics)

    def test_default_works(self) -> None:
        d = get_default_analytics()
        r = d.query("SELECT 42 AS answer")
        assert r == [{"answer": 42}]


class TestPostgresAttach:
    """Postgres attach (tested with non-existent URL to verify graceful failure)."""

    def test_attach_missing_pg_url_fails(self, db, monkeypatch) -> None:
        """If PG_URL points nowhere, ATTACH should raise (not silently swallow)."""
        monkeypatch.setenv("PG_URL", "postgres://nobody:nope@localhost:1/none")
        with pytest.raises(Exception):
            db.query_postgres("SELECT 1")
