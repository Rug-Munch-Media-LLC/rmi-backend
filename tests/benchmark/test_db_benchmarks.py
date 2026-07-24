"""Database query performance benchmarks.

Every test tracks execution time with time.perf_counter() and asserts
the result is below a defined threshold. Tests skip gracefully when
the target database is not available.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

# ═══════════════════════════════════════════════════════════════════════
# PostgreSQL Benchmarks
# ═══════════════════════════════════════════════════════════════════════

THRESHOLD_PG_PK_LOOKUP_MS = 50.0
THRESHOLD_PG_INDEX_LOOKUP_MS = 100.0
THRESHOLD_PG_INSERT_MS = 100.0
THRESHOLD_PG_UPDATE_MS = 100.0


class TestPostgresBenchmarks:
    """PostgreSQL query benchmarks — SELECT, INSERT, UPDATE performance."""

    @pytest.mark.asyncio
    @pytest.mark.benchmark
    async def test_pg_select_by_primary_key(
        self, pg_conn: Any, assert_time: Any, timer: Any
    ) -> None:
        """Benchmark: PostgreSQL SELECT by primary key must complete < 50ms.

        Creates a test table, inserts a row, then measures how long it
        takes to fetch that row by primary key.
        """
        await pg_conn.execute("""
            CREATE TEMP TABLE IF NOT EXISTS bm_pk_test (
                id SERIAL PRIMARY KEY,
                label TEXT NOT NULL,
                value BIGINT NOT NULL DEFAULT 0
            )
        """)
        await pg_conn.execute(
            "INSERT INTO bm_pk_test (label, value) VALUES ($1, $2)",
            "benchmark_row",
            42,
        )

        timer.start()
        row = await pg_conn.fetchrow(
            "SELECT id, label, value FROM bm_pk_test WHERE id = 1",
        )
        elapsed_ms = timer.stop() * 1000

        assert row is not None, "SELECT by PK returned no rows"
        assert row["label"] == "benchmark_row"
        assert_time(elapsed_ms, THRESHOLD_PG_PK_LOOKUP_MS, "pg_select_pk")

    @pytest.mark.asyncio
    @pytest.mark.benchmark
    async def test_pg_select_by_index(self, pg_conn: Any, assert_time: Any, timer: Any) -> None:
        """Benchmark: PostgreSQL SELECT by indexed column < 100ms.

        Creates a table with an index on a text column, inserts rows,
        then queries by that column. The index should make this fast.
        """
        await pg_conn.execute("""
            CREATE TEMP TABLE IF NOT EXISTS bm_index_test (
                id SERIAL PRIMARY KEY,
                wallet_address TEXT NOT NULL,
                balance BIGINT NOT NULL DEFAULT 0
            )
        """)
        await pg_conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bm_wallet
            ON bm_index_test (wallet_address)
        """)
        # Insert test rows
        for i in range(100):
            await pg_conn.execute(
                "INSERT INTO bm_index_test (wallet_address, balance) VALUES ($1, $2)",
                f"0xdeadbeef{i:04d}000000000000000000000000",
                i * 1000,
            )

        timer.start()
        row = await pg_conn.fetchrow(
            "SELECT id, wallet_address, balance FROM bm_index_test WHERE wallet_address = $1",
            f"0xdeadbeef{42:04d}000000000000000000000000",
        )
        elapsed_ms = timer.stop() * 1000

        assert row is not None, "SELECT by index returned no rows"
        assert_time(elapsed_ms, THRESHOLD_PG_INDEX_LOOKUP_MS, "pg_select_index")

    @pytest.mark.asyncio
    @pytest.mark.benchmark
    async def test_pg_insert(self, pg_conn: Any, assert_time: Any, timer: Any) -> None:
        """Benchmark: PostgreSQL single row INSERT < 100ms.

        Measures the round-trip time for inserting a row into a table
        with primary key and returning the generated ID.
        """
        await pg_conn.execute("""
            CREATE TEMP TABLE IF NOT EXISTS bm_insert_test (
                id SERIAL PRIMARY KEY,
                chain TEXT NOT NULL,
                address TEXT NOT NULL,
                scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                data JSONB NOT NULL DEFAULT '{}'
            )
        """)

        timer.start()
        row = await pg_conn.fetchrow(
            "INSERT INTO bm_insert_test (chain, address, data) VALUES ($1, $2, $3) RETURNING id",
            "solana",
            "BMInsertTestAddr0000000000000000000000000",
            '{"risk_score": 42, "flags": ["test"]}',
        )
        elapsed_ms = timer.stop() * 1000

        assert row is not None, "INSERT RETURNING returned no id"
        assert row["id"] > 0
        assert_time(elapsed_ms, THRESHOLD_PG_INSERT_MS, "pg_insert")

    @pytest.mark.asyncio
    @pytest.mark.benchmark
    async def test_pg_update(self, pg_conn: Any, assert_time: Any, timer: Any) -> None:
        """Benchmark: PostgreSQL single row UPDATE < 100ms.

        Measures round-trip for UPDATE with RETURNING to verify the
        update took effect.
        """
        await pg_conn.execute("""
            CREATE TEMP TABLE IF NOT EXISTS bm_update_test (
                id SERIAL PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                score INT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await pg_conn.execute(
            "INSERT INTO bm_update_test (status, score) VALUES ($1, $2)",
            "pending",
            10,
        )

        timer.start()
        row = await pg_conn.fetchrow(
            "UPDATE bm_update_test SET status = $1, score = $2 "
            "WHERE id = 1 RETURNING id, status, score",
            "completed",
            95,
        )
        elapsed_ms = timer.stop() * 1000

        assert row is not None, "UPDATE RETURNING returned no row"
        assert row["status"] == "completed"
        assert row["score"] == 95
        assert_time(elapsed_ms, THRESHOLD_PG_UPDATE_MS, "pg_update")


# ═══════════════════════════════════════════════════════════════════════
# ClickHouse Benchmarks
# ═══════════════════════════════════════════════════════════════════════

THRESHOLD_CH_AGGREGATE_MS = 200.0
THRESHOLD_CH_TIME_RANGE_MS = 200.0


class TestClickHouseBenchmarks:
    """ClickHouse query benchmarks — aggregate and time-range queries."""

    @pytest.mark.benchmark
    def test_ch_aggregate_query(self, ch_conn: Any, assert_time: Any, timer: Any) -> None:
        """Benchmark: ClickHouse aggregate query < 200ms.

        Creates an in-memory table, inserts sample event rows, then
        runs a GROUP BY aggregation that mirrors real analytics queries.
        """
        ch_conn.execute("""
            CREATE TEMPORARY TABLE bm_events (
                event_type String,
                chain String,
                value Float64,
                ts DateTime
            ) ENGINE = Memory
        """)
        import datetime

        now = datetime.datetime.utcnow()
        rows: list[tuple[str, str, float, datetime.datetime]] = []
        for i in range(500):
            rows.append(
                (
                    "scan" if i % 3 == 0 else "alert" if i % 3 == 1 else "webhook",
                    ["solana", "ethereum", "base"][i % 3],
                    float(i * 1.5),
                    now - datetime.timedelta(minutes=i),
                )
            )
        ch_conn.execute(
            "INSERT INTO bm_events (event_type, chain, value, ts) VALUES",
            rows,
        )

        timer.start()
        result = ch_conn.execute("""
            SELECT
                event_type,
                chain,
                count() AS cnt,
                avg(value) AS avg_val,
                max(value) AS max_val
            FROM bm_events
            GROUP BY event_type, chain
            ORDER BY cnt DESC
        """)
        elapsed_ms = timer.stop() * 1000

        assert len(result) > 0, "Aggregate query returned no rows"
        # Verify structure: each row has 5 columns
        for row in result:
            assert len(row) == 5, f"Expected 5 columns, got {len(row)}"
        assert_time(elapsed_ms, THRESHOLD_CH_AGGREGATE_MS, "ch_aggregate_query")

    @pytest.mark.benchmark
    def test_ch_time_range_query(self, ch_conn: Any, assert_time: Any, timer: Any) -> None:
        """Benchmark: ClickHouse time-range filtered query < 200ms.

        Queries with WHERE on DateTime column to simulate real
        analytics dashboard queries that filter by time window.
        """
        ch_conn.execute("""
            CREATE TEMPORARY TABLE bm_time_series (
                chain String,
                address String,
                event_type String,
                value Float64,
                ts DateTime
            ) ENGINE = Memory
        """)
        import datetime

        now = datetime.datetime.utcnow()
        rows: list[tuple[str, str, str, float, datetime.datetime]] = []
        for i in range(1000):
            rows.append(
                (
                    ["solana", "ethereum", "base", "bsc", "polygon"][i % 5],
                    f"0x{i:040x}",
                    ["transfer", "swap", "approve", "mint"][i % 4],
                    float(i * 0.001),
                    now - datetime.timedelta(hours=i),
                )
            )
        ch_conn.execute(
            "INSERT INTO bm_time_series (chain, address, event_type, value, ts) VALUES",
            rows,
        )

        timer.start()
        result = ch_conn.execute("""
            SELECT chain, count() AS tx_count, sum(value) AS total_value
            FROM bm_time_series
            WHERE ts >= now() - INTERVAL 24 HOUR
              AND ts <= now()
            GROUP BY chain
            ORDER BY total_value DESC
        """)
        elapsed_ms = timer.stop() * 1000

        assert len(result) > 0, "Time-range query returned no rows"
        assert_time(elapsed_ms, THRESHOLD_CH_TIME_RANGE_MS, "ch_time_range_query")


# ═══════════════════════════════════════════════════════════════════════
# Redis Benchmarks
# ═══════════════════════════════════════════════════════════════════════

THRESHOLD_REDIS_SET_MS = 10.0
THRESHOLD_REDIS_GET_MS = 10.0
THRESHOLD_REDIS_DELETE_MS = 10.0
THRESHOLD_REDIS_PIPELINE_MS = 50.0


class TestRedisBenchmarks:
    """Redis roundtrip and pipeline throughput benchmarks."""

    @pytest.mark.benchmark
    def test_redis_set_roundtrip(self, redis_conn: Any, assert_time: Any, timer: Any) -> None:
        """Benchmark: Redis SET roundtrip < 10ms.

        Sets a string value and verifies it was stored by checking
        the response from the Redis server.
        """
        timer.start()
        result = redis_conn.set("bm:set:key1", "benchmark_value_12345")
        elapsed_ms = timer.stop() * 1000

        assert result is True, "Redis SET did not return True"
        assert_time(elapsed_ms, THRESHOLD_REDIS_SET_MS, "redis_set")

    @pytest.mark.benchmark
    def test_redis_get_roundtrip(self, redis_conn: Any, assert_time: Any, timer: Any) -> None:
        """Benchmark: Redis GET roundtrip < 10ms.

        Sets a key first, then measures how long it takes to retrieve
        and verify the value.
        """
        redis_conn.set("bm:get:key1", "benchmark_get_value")
        timer.start()
        value = redis_conn.get("bm:get:key1")
        elapsed_ms = timer.stop() * 1000

        assert value is not None, "Redis GET returned None"
        assert value.decode() == "benchmark_get_value"
        assert_time(elapsed_ms, THRESHOLD_REDIS_GET_MS, "redis_get")

    @pytest.mark.benchmark
    def test_redis_delete_roundtrip(self, redis_conn: Any, assert_time: Any, timer: Any) -> None:
        """Benchmark: Redis DELETE roundtrip < 10ms.

        Sets a key, then measures how long deletion takes, and verifies
        the key no longer exists.
        """
        redis_conn.set("bm:del:key1", "to_be_deleted")
        timer.start()
        deleted_count = redis_conn.delete("bm:del:key1")
        elapsed_ms = timer.stop() * 1000

        assert deleted_count == 1, f"Expected 1 deleted, got {deleted_count}"
        exists = redis_conn.exists("bm:del:key1")
        assert exists == 0, "Key still exists after DELETE"
        assert_time(elapsed_ms, THRESHOLD_REDIS_DELETE_MS, "redis_delete")

    @pytest.mark.benchmark
    def test_redis_pipeline_throughput(self, redis_conn: Any, assert_time: Any, timer: Any) -> None:
        """Benchmark: Redis pipeline with 100 operations < 50ms.

        Uses a pipeline to batch 100 SET commands and execute them
        in a single roundtrip. Measures total time and verifies
        all operations succeeded.
        """
        pipe = redis_conn.pipeline()
        for i in range(100):
            pipe.set(f"bm:pipeline:key{i}", f"value_{i}")
        timer.start()
        results = pipe.execute()
        elapsed_ms = timer.stop() * 1000

        assert len(results) == 100, f"Expected 100 pipeline results, got {len(results)}"
        assert all(r is True for r in results), "Not all pipeline SET operations succeeded"
        assert_time(elapsed_ms, THRESHOLD_REDIS_PIPELINE_MS, "redis_pipeline")

        # Cleanup pipeline keys
        cleanup = redis_conn.pipeline()
        for i in range(100):
            cleanup.delete(f"bm:pipeline:key{i}")
        cleanup.execute()
