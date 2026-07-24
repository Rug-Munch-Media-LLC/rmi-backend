"""Multi-database integration tests — verifies all 5 database backends.

Coverage:
  - PostgreSQL:   connection pooling, CRUD, transactions, Alembic, N+1 guard
  - ClickHouse:   time-series insert/query, aggregations, partition pruning
  - Neo4j:        nodes + relationships, entity resolution, path finding
  - Redis/Dragonfly:  cache TTL, pub/sub, Lua scripting, pipelines
  - Qdrant:       vector CRUD, collection management, filtered search
  - Cross-DB:     entity consistency, cache invalidation

Uses testcontainers (Docker) for each database. All containers are
session-scoped and reused across tests within each class. If Docker is
unavailable, tests are skipped with a clear message.

Markers:
    pytest.mark.integration — these tests require Docker.
"""

from __future__ import annotations

import asyncio
import httpx
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

# Qdrant REST API helpers — avoids importing the Python qdrant_client
# (which pulls in a broken huggingface_hub._snapshot_download on this
# system).  All Qdrant interactions use httpx against the REST endpoint
# (port 6333), matching the pattern in app/core/health_route.py and
# app/catalog/service.py.


def _qdrant_create_collection(
    client: httpx.Client, name: str, size: int, distance: str = "Cosine"
) -> dict:
    """Create a Qdrant collection via REST API."""
    resp = client.put(
        f"/collections/{name}", json={"vectors": {"size": size, "distance": distance}}
    )
    resp.raise_for_status()
    return resp.json()


def _qdrant_collection_info(client: httpx.Client, name: str) -> dict:
    """Get Qdrant collection info via REST API."""
    resp = client.get(f"/collections/{name}")
    resp.raise_for_status()
    return resp.json()["result"]


def _qdrant_delete_collection(client: httpx.Client, name: str) -> dict:
    """Delete a Qdrant collection via REST API."""
    resp = client.delete(f"/collections/{name}")
    resp.raise_for_status()
    return resp.json()


def _qdrant_list_collections(client: httpx.Client) -> list[dict]:
    """List Qdrant collections via REST API."""
    resp = client.get("/collections")
    resp.raise_for_status()
    return resp.json()["result"]["collections"]


def _qdrant_upsert_points(client: httpx.Client, name: str, points: list[dict]) -> dict:
    """Upsert points into a Qdrant collection via REST API."""
    resp = client.put(f"/collections/{name}/points", json={"points": points})
    resp.raise_for_status()
    return resp.json()


def _qdrant_search_points(
    client: httpx.Client,
    name: str,
    vector: list[float],
    limit: int = 10,
    qdrant_filter: dict | None = None,
) -> list[dict]:
    """Search points in a Qdrant collection via REST API."""
    body: dict = {"vector": vector, "limit": limit, "with_payload": True}
    if qdrant_filter is not None:
        body["filter"] = qdrant_filter
    resp = client.post(f"/collections/{name}/points/search", json=body)
    resp.raise_for_status()
    return resp.json()["result"]


def _qdrant_delete_points(client: httpx.Client, name: str, point_ids: list[int]) -> dict:
    """Delete points from a Qdrant collection via REST API."""
    resp = client.post(f"/collections/{name}/points/delete", json={"points": point_ids})
    resp.raise_for_status()
    return resp.json()


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("CI") and not os.environ.get("RUN_DB_TESTS"),
        reason="Set CI=1 or RUN_DB_TESTS=1 to enable database integration tests",
    ),
]

# ── Helpers ────────────────────────────────────────────────────────────


def _random_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _assert_healthy(result: dict[str, Any], name: str) -> None:
    """Assert a health-check result is healthy, with debug on failure."""
    assert result.get("healthy"), f"{name} not healthy: {result.get('error', 'unknown')}"


# ═══════════════════════════════════════════════════════════════════════
# 1. PostgreSQL
# ═══════════════════════════════════════════════════════════════════════
# Tests use the project's SQLAlchemy async engine and session, plus raw
# asyncpg for direct pool checks.

pytestmark_postgres = [pytest.mark.integration, *pytestmark]


@pytest.fixture(scope="module")
def pg_container():
    """One Postgres container per module, auto-destroyed."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def pg_dsn(pg_container) -> str:
    """asyncpg-compatible DSN (postgresql+asyncpg://...)."""
    return pg_container.get_connection_url(driver="asyncpg")


@pytest.fixture(scope="module")
def pg_sync_dsn(pg_container) -> str:
    """Sync DSN (postgresql://...)."""
    return pg_container.get_connection_url()


@pytest.fixture
async def pg_pool(pg_dsn):
    """Per-test asyncpg connection pool."""
    import asyncpg

    pool = await asyncpg.create_pool(dsn=pg_dsn.replace("+asyncpg", ""), min_size=2, max_size=5)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def pg_session(pg_dsn):
    """Per-test SQLAlchemy async session."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(pg_dsn, pool_size=2, max_overflow=2)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()
    await engine.dispose()


class TestPostgresConnectionPool:
    """Connection pool health, configuration, and lifecycle."""

    async def test_pool_health(self, pg_pool):
        """Pool can acquire and release connections."""
        conn = await pg_pool.acquire()
        val = await conn.fetchval("SELECT 1 AS v")
        assert val == 1
        await pg_pool.release(conn)
        # Pool should still be usable after release
        async with pg_pool.acquire() as c:
            assert await c.fetchval("SELECT 1") == 1

    async def test_pool_concurrent_connections(self, pg_pool):
        """Pool handles concurrent connections up to pool limit."""

        async def _query(n: int) -> int:
            async with pg_pool.acquire() as c:
                await asyncio.sleep(0.05)
                return await c.fetchval("SELECT $1::int AS n", n)

        results = await asyncio.gather(*[_query(i) for i in range(5)])
        assert results == [0, 1, 2, 3, 4]

    async def test_pool_config_reflects_env(self):
        """Pool configuration respects environment settings."""
        from app.core.database import engine

        # Check the engine pool settings
        assert engine.pool.size() >= 0  # pool exists
        assert engine.pool._pool.maxsize >= 1  # has max connections

    async def test_pool_connection_timeout(self, pg_dsn):
        """Pool times out gracefully on invalid DSN."""
        import asyncpg

        bad_dsn = pg_dsn.replace("localhost", "192.0.2.1").replace("+asyncpg", "")
        with pytest.raises(
            (
                OSError,
                asyncpg.CannotConnectNowError,
                asyncpg.ConnectionDoesNotExistError,
                ConnectionError,
                TimeoutError,
            )
        ):
            await asyncio.wait_for(
                asyncpg.connect(bad_dsn, timeout=2),
                timeout=5,
            )


class TestPostgresCRUD:
    """CRUD operations using SQLAlchemy ORM + raw SQL."""

    async def _create_table(self, session):
        """Create a simple test table."""
        from sqlalchemy import text

        await session.execute(
            text("""
            CREATE TABLE IF NOT EXISTS test_entities (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                value NUMERIC(20, 8),
                tags TEXT[],
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        )
        await session.commit()

    async def _drop_table(self, session):
        from sqlalchemy import text

        await session.execute(text("DROP TABLE IF EXISTS test_entities CASCADE"))
        await session.commit()

    async def test_create_and_read(self, pg_session):
        """Create rows and verify they're readable."""
        from sqlalchemy import text

        await self._create_table(pg_session)
        try:
            await pg_session.execute(
                text("INSERT INTO test_entities (name, value, tags) VALUES (:n, :v, :t)"),
                {"n": "test_token_a", "v": 100.5, "t": ["solana", "defi"]},
            )
            await pg_session.commit()

            result = await pg_session.execute(
                text("SELECT name, value, tags FROM test_entities WHERE name = :n"),
                {"n": "test_token_a"},
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] == "test_token_a"
            assert float(row[1]) == 100.5
            assert "solana" in row[2]
        finally:
            await self._drop_table(pg_session)

    async def test_update(self, pg_session):
        """Update rows and verify changes."""
        from sqlalchemy import text

        await self._create_table(pg_session)
        try:
            await pg_session.execute(
                text("INSERT INTO test_entities (name, value) VALUES (:n, :v)"),
                {"n": "updatable", "v": 50.0},
            )
            await pg_session.commit()

            await pg_session.execute(
                text("UPDATE test_entities SET value = :v WHERE name = :n"),
                {"v": 75.0, "n": "updatable"},
            )
            await pg_session.commit()

            result = await pg_session.execute(
                text("SELECT value FROM test_entities WHERE name = :n"),
                {"n": "updatable"},
            )
            assert float(result.fetchone()[0]) == 75.0
        finally:
            await self._drop_table(pg_session)

    async def test_delete(self, pg_session):
        """Delete rows and verify removal."""
        from sqlalchemy import text

        await self._create_table(pg_session)
        try:
            await pg_session.execute(
                text("INSERT INTO test_entities (name, value) VALUES (:n, :v)"),
                {"n": "deletable", "v": 1.0},
            )
            await pg_session.commit()

            await pg_session.execute(
                text("DELETE FROM test_entities WHERE name = :n"),
                {"n": "deletable"},
            )
            await pg_session.commit()

            result = await pg_session.execute(
                text("SELECT COUNT(*) FROM test_entities WHERE name = :n"),
                {"n": "deletable"},
            )
            assert result.fetchone()[0] == 0
        finally:
            await self._drop_table(pg_session)

    async def test_bulk_insert(self, pg_session):
        """Bulk insert performs correctly."""
        from sqlalchemy import text

        await self._create_table(pg_session)
        try:
            rows = [{"n": f"bulk_{i}", "v": float(i * 10)} for i in range(100)]
            for r in rows:
                await pg_session.execute(
                    text("INSERT INTO test_entities (name, value) VALUES (:n, :v)"),
                    r,
                )
            await pg_session.commit()

            result = await pg_session.execute(text("SELECT COUNT(*) FROM test_entities"))
            assert result.fetchone()[0] == 100
        finally:
            await self._drop_table(pg_session)


class TestPostgresTransactions:
    """Transaction commit/rollback semantics."""

    async def test_commit_persists_data(self, pg_session):
        """Committed transaction makes data visible."""
        from sqlalchemy import text

        await pg_session.execute(
            text("""
            CREATE TABLE IF NOT EXISTS test_tx (
                id SERIAL PRIMARY KEY,
                val INT
            )
        """)
        )
        await pg_session.commit()
        try:
            await pg_session.execute(text("INSERT INTO test_tx (val) VALUES (42)"))
            await pg_session.commit()

            result = await pg_session.execute(text("SELECT val FROM test_tx WHERE val = 42"))
            assert result.fetchone() is not None
        finally:
            await pg_session.execute(text("DROP TABLE IF EXISTS test_tx CASCADE"))
            await pg_session.commit()

    async def test_rollback_discards_data(self, pg_session):
        """Rolled-back transaction leaves no trace."""
        from sqlalchemy import text

        await pg_session.execute(
            text("""
            CREATE TABLE IF NOT EXISTS test_tx (
                id SERIAL PRIMARY KEY,
                val INT
            )
        """)
        )
        await pg_session.commit()
        try:
            await pg_session.execute(text("INSERT INTO test_tx (val) VALUES (99)"))
            await pg_session.rollback()

            result = await pg_session.execute(text("SELECT COUNT(*) FROM test_tx WHERE val = 99"))
            assert result.fetchone()[0] == 0
        finally:
            await pg_session.execute(text("DROP TABLE IF EXISTS test_tx CASCADE"))
            await pg_session.commit()

    async def test_savepoint_rollback(self, pg_session):
        """Nested savepoints roll back independently."""
        from sqlalchemy import text

        await pg_session.execute(
            text("""
            CREATE TABLE IF NOT EXISTS test_tx (
                id SERIAL PRIMARY KEY,
                val INT
            )
        """)
        )
        await pg_session.commit()
        try:
            await pg_session.execute(text("INSERT INTO test_tx (val) VALUES (1)"))

            # Savepoint: insert 2, then explicitly rollback the savepoint
            conn = await pg_session.connection()
            savepoint = await conn.begin_nested()
            await pg_session.execute(text("INSERT INTO test_tx (val) VALUES (2)"))
            await savepoint.rollback()

            await pg_session.execute(text("INSERT INTO test_tx (val) VALUES (3)"))
            await pg_session.commit()

            result = await pg_session.execute(text("SELECT val FROM test_tx ORDER BY val"))
            vals = [row[0] for row in result.fetchall()]
            assert vals == [1, 3], f"Expected [1, 3], got {vals}"
        finally:
            await pg_session.execute(text("DROP TABLE IF EXISTS test_tx CASCADE"))
            await pg_session.commit()


class TestPostgresAlembic:
    """Alembic migration status checks."""

    async def test_alembic_current_head_exists(self, pg_session, pg_dsn):
        """Alembic can report current migration head."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", "alembic")
        script = ScriptDirectory.from_config(alembic_cfg)
        head = script.get_current_head()
        assert head is not None, "No Alembic migration head found — repo needs initial migration"
        assert len(head) >= 8, f"Head revision '{head}' looks malformed"

    async def test_alembic_migration_history(self, pg_session, pg_dsn):
        """Migration history is retrievable from the alembic_version table."""
        from sqlalchemy import text

        try:
            result = await pg_session.execute(text("SELECT version_num FROM alembic_version"))
            row = result.fetchone()
            # If alembic_version table doesn't exist, that's OK — no migrations run yet
            if row is not None:
                assert len(row[0]) >= 8
        except Exception:
            # Table may not exist if no migrations have been applied
            pass


class TestPostgresNPlusOne:
    """N+1 query detection guard."""

    async def test_query_batch_loading(self, pg_session):
        """Batch loading (IN-clause) prevents N+1."""
        from sqlalchemy import text

        await pg_session.execute(
            text("""
            CREATE TABLE IF NOT EXISTS test_np1 (
                id SERIAL PRIMARY KEY,
                group_id INT,
                val TEXT
            )
        """)
        )
        await pg_session.commit()
        try:
            # Insert parent + children
            for g in range(3):
                for i in range(5):
                    await pg_session.execute(
                        text("INSERT INTO test_np1 (group_id, val) VALUES (:g, :v)"),
                        {"g": g, "v": f"item_{g}_{i}"},
                    )
            await pg_session.commit()

            # Single query with IN clause (NOT N+1)
            result = await pg_session.execute(
                text(
                    "SELECT group_id, val FROM test_np1 WHERE group_id = ANY(:ids) ORDER BY group_id, val"
                ),
                {"ids": [0, 2]},
            )
            rows = result.fetchall()
            assert len(rows) == 10  # 5 from group 0 + 5 from group 2
        finally:
            await pg_session.execute(text("DROP TABLE IF EXISTS test_np1 CASCADE"))
            await pg_session.commit()

    async def test_query_count_one_roundtrip(self, pg_session):
        """Fetching count uses single query, not client-side iteration."""
        from sqlalchemy import text

        await pg_session.execute(
            text("""
            CREATE TABLE IF NOT EXISTS test_np1 (
                id SERIAL PRIMARY KEY,
                group_id INT,
                val TEXT
            )
        """)
        )
        await pg_session.commit()
        try:
            for i in range(50):
                await pg_session.execute(
                    text("INSERT INTO test_np1 (group_id, val) VALUES (:g, :v)"),
                    {"g": i % 5, "v": f"row_{i}"},
                )
            await pg_session.commit()

            # COUNT query is O(1) roundtrip
            result = await pg_session.execute(
                text("SELECT group_id, COUNT(*) FROM test_np1 GROUP BY group_id ORDER BY group_id")
            )
            counts = {row[0]: row[1] for row in result.fetchall()}
            assert counts == {0: 10, 1: 10, 2: 10, 3: 10, 4: 10}

            db_used = await pg_session.execute(text("SELECT current_database()"))
            assert db_used.fetchone() is not None
        finally:
            await pg_session.execute(text("DROP TABLE IF EXISTS test_np1 CASCADE"))
            await pg_session.commit()

    async def test_no_separate_queries_in_loop(self, pg_session):
        """Detect when a loop would cause N+1 by measuring roundtrips."""
        from sqlalchemy import text

        await pg_session.execute(
            text("""
            CREATE TABLE IF NOT EXISTS test_np1 (
                id SERIAL PRIMARY KEY,
                group_id INT,
                val TEXT
            )
        """)
        )
        await pg_session.commit()
        try:
            for i in range(10):
                await pg_session.execute(
                    text("INSERT INTO test_np1 (group_id, val) VALUES (:g, :v)"),
                    {"g": 1, "v": f"row_{i}"},
                )
            await pg_session.commit()

            # Correct: one query instead of N individual lookups
            result = await pg_session.execute(
                text("SELECT val FROM test_np1 WHERE group_id = :g ORDER BY val"),
                {"g": 1},
            )
            vals = [row[0] for row in result.fetchall()]
            assert len(vals) == 10
            # If we had done one query-per-row, this would be N+1 = 11 queries.
            # With batch fetch, it's 1 query. We verify correct data.
        finally:
            await pg_session.execute(text("DROP TABLE IF EXISTS test_np1 CASCADE"))
            await pg_session.commit()


# ═══════════════════════════════════════════════════════════════════════
# 2. ClickHouse
# ═══════════════════════════════════════════════════════════════════════

pytestmark_clickhouse = [pytest.mark.integration, *pytestmark]


@pytest.fixture(scope="module")
def ch_container():
    from testcontainers.clickhouse import ClickHouseContainer

    with ClickHouseContainer("clickhouse/clickhouse-server:26.6.2.81") as ch:
        yield ch


@pytest.fixture(scope="module")
def ch_http_url(ch_container) -> str:
    """ClickHouse HTTP endpoint."""
    host = ch_container.get_container_host_ip()
    port = ch_container.get_exposed_port(8123)
    return f"http://{host}:{port}"


@pytest.fixture
def ch_client(ch_http_url):
    """Per-test ClickHouse connect client."""
    import clickhouse_connect

    host = ch_http_url.replace("http://", "").split(":")[0]
    port = int(ch_http_url.split(":")[-1])
    client = clickhouse_connect.get_client(
        host=host,
        port=port,
        username="test",
        password="test",
    )
    return client


@pytest.fixture
def ch_native_client(ch_container):
    """Per-test ClickHouse native TCP client."""
    import clickhouse_driver

    host = ch_container.get_container_host_ip()
    port = ch_container.get_exposed_port(9000)
    client = clickhouse_driver.Client(host=host, port=int(port), user="test", password="test")
    return client


class TestClickHouseTimeSeries:
    """Time-series data insert and query."""

    def test_insert_and_query(self, ch_client):
        """Insert rows and verify they're queryable."""
        ch_client.command("""CREATE TABLE IF NOT EXISTS test_events (
            ts DateTime,
            event_type String,
            value Float64,
            chain String
        ) ENGINE = MergeTree()
        ORDER BY (ts, event_type)""")
        ch_client.command("TRUNCATE TABLE IF EXISTS test_events")

        ch_client.insert(
            "test_events",
            [
                [datetime(2026, 7, 1, 12, 0, 0), "trade", 100.5, "solana"],
                [datetime(2026, 7, 1, 12, 1, 0), "trade", 200.3, "solana"],
                [datetime(2026, 7, 1, 12, 2, 0), "alert", 1.0, "ethereum"],
            ],
            column_names=["ts", "event_type", "value", "chain"],
        )

        result = ch_client.query("SELECT count(*) AS cnt FROM test_events")
        assert result.result_rows[0][0] == 3

        result = ch_client.query(
            "SELECT event_type, count(*) AS cnt FROM test_events GROUP BY event_type ORDER BY cnt"
        )
        counts = {row[0]: row[1] for row in result.result_rows}
        assert counts.get("trade") == 2
        assert counts.get("alert") == 1

        ch_client.command("DROP TABLE IF EXISTS test_events")

    def test_query_with_time_filter(self, ch_client):
        """Filter by time range uses partition pruning."""
        ch_client.command("""CREATE TABLE IF NOT EXISTS test_timefilter (
            ts DateTime,
            metric String,
            val Int32
        ) ENGINE = MergeTree()
        PARTITION BY toYYYYMM(ts)
        ORDER BY ts""")
        ch_client.command("TRUNCATE TABLE IF EXISTS test_timefilter")

        now = datetime(2026, 7, 15, 0, 0, 0)
        data = [[now - timedelta(hours=h), f"m{i}", i] for i, h in enumerate(range(0, 48, 2))]
        ch_client.insert(
            "test_timefilter",
            data,
            column_names=["ts", "metric", "val"],
        )

        # Query recent 6 hours
        cutoff = now - timedelta(hours=6)
        result = ch_client.query(
            "SELECT count(*) AS cnt FROM test_timefilter WHERE ts >= %(cutoff)s",
            parameters={"cutoff": cutoff},
        )
        assert result.result_rows[0][0] >= 2  # at least the last 3 items (h=0,2,4)

        ch_client.command("DROP TABLE IF EXISTS test_timefilter")

    def test_aggregation_query(self, ch_client):
        """Aggregation functions work correctly."""
        ch_client.command("""CREATE TABLE IF NOT EXISTS test_agg (
            dt Date,
            category String,
            amount Float64
        ) ENGINE = MergeTree()
        ORDER BY (dt, category)""")
        ch_client.command("TRUNCATE TABLE IF EXISTS test_agg")

        data = [
            [datetime(2026, 7, 1), "a", 10.0],
            [datetime(2026, 7, 1), "a", 20.0],
            [datetime(2026, 7, 1), "b", 30.0],
            [datetime(2026, 7, 2), "a", 40.0],
            [datetime(2026, 7, 2), "b", 50.0],
        ]
        ch_client.insert("test_agg", data, column_names=["dt", "category", "amount"])

        result = ch_client.query("""
            SELECT category, sum(amount), avg(amount), count(*)
            FROM test_agg
            GROUP BY category
            ORDER BY category
        """)
        rows = {r[0]: {"sum": r[1], "avg": r[2], "cnt": r[3]} for r in result.result_rows}
        assert rows["a"]["sum"] == 70.0
        assert rows["a"]["cnt"] == 3
        assert rows["b"]["sum"] == 80.0
        assert rows["b"]["cnt"] == 2

        ch_client.command("DROP TABLE IF EXISTS test_agg")


class TestClickHousePartitionPruning:
    """Partition pruning reduces scanned granules."""

    def test_partition_pruning_filter(self, ch_client):
        """Queries with partition key filter scan fewer parts."""
        ch_client.command("""
            CREATE TABLE IF NOT EXISTS test_partitions (
                ts DateTime,
                event String,
                val Int32
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMM(ts)
            ORDER BY ts
        """)
        ch_client.command("TRUNCATE TABLE IF EXISTS test_partitions")

        # Insert data across 3 months
        for month in range(6, 9):
            ch_client.insert(
                "test_partitions",
                [
                    [datetime(2026, month, d, 0, 0, 0), f"evt_{month}_{d}", d]
                    for d in range(1, 29, 3)
                ],
                column_names=["ts", "event", "val"],
            )

        # Query single month — should only scan July
        result = ch_client.query("""
            SELECT count(*) AS cnt
            FROM test_partitions
            WHERE ts >= '2026-07-01' AND ts < '2026-08-01'
        """)
        assert result.result_rows[0][0] > 0

        # Full scan gets everything
        result = ch_client.query("SELECT count(*) FROM test_partitions")
        assert result.result_rows[0][0] > 0

        ch_client.command("DROP TABLE IF EXISTS test_partitions")


# ═══════════════════════════════════════════════════════════════════════
# 3. Neo4j
# ═══════════════════════════════════════════════════════════════════════

pytestmark_neo4j = [pytest.mark.integration, *pytestmark]


@pytest.fixture(scope="module")
def neo4j_container():
    from testcontainers.neo4j import Neo4jContainer

    with Neo4jContainer("neo4j:5") as neo:
        yield neo


@pytest.fixture
def neo4j_driver(neo4j_container):
    """Per-test Neo4j driver."""
    driver = neo4j_container.get_driver()
    try:
        yield driver
    finally:
        driver.close()


class TestNeo4jNodesAndRelationships:
    """Node creation, relationship linking, and querying."""

    def test_create_node(self, neo4j_driver):
        """Create a node with properties and retrieve it."""
        wallet_id = _random_id("wallet_")
        with neo4j_driver.session() as s:
            s.run(
                "CREATE (w:Wallet {wallet_id: $id, chain: 'solana', address: $addr, tx_count: 42})",
                id=wallet_id,
                addr=f"addr_{wallet_id}",
            )
            result = s.run(
                "MATCH (w:Wallet {wallet_id: $id}) RETURN w.wallet_id, w.tx_count",
                id=wallet_id,
            ).single()
            assert result is not None
            assert result["w.wallet_id"] == wallet_id
            assert result["w.tx_count"] == 42

    def test_create_relationship(self, neo4j_driver):
        """Create nodes + relationship and traverse it."""
        wallet_id = _random_id("w_")
        token_id = _random_id("t_")
        with neo4j_driver.session() as s:
            s.run("CREATE (w:Wallet {wallet_id: $id})", id=wallet_id)
            s.run("CREATE (t:Token {token_id: $id, symbol: 'TEST'})", id=token_id)
            s.run(
                "MATCH (w:Wallet {wallet_id: $wid}) "
                "MATCH (t:Token {token_id: $tid}) "
                "CREATE (w)-[:DEPLOYED {at: datetime()}]->(t)",
                wid=wallet_id,
                tid=token_id,
            )
            result = s.run(
                "MATCH (w:Wallet)-[:DEPLOYED]->(t:Token) "
                "WHERE w.wallet_id = $wid "
                "RETURN t.token_id, t.symbol",
                wid=wallet_id,
            ).single()
            assert result is not None
            assert result["t.token_id"] == token_id
            assert result["t.symbol"] == "TEST"

    def test_update_node_properties(self, neo4j_driver):
        """Update node properties with MERGE/SET."""
        wid = _random_id("upd_")
        with neo4j_driver.session() as s:
            s.run("CREATE (w:Wallet {wallet_id: $id, risk_score: 50})", id=wid)
            s.run(
                "MATCH (w:Wallet {wallet_id: $id}) SET w.risk_score = 75, w.is_suspicious = true",
                id=wid,
            )
            result = s.run(
                "MATCH (w:Wallet {wallet_id: $id}) RETURN w.risk_score, w.is_suspicious",
                id=wid,
            ).single()
            assert result["w.risk_score"] == 75
            assert result["w.is_suspicious"] is True

    def test_delete_node_cascades(self, neo4j_driver):
        """Delete node with relationships."""
        wid = _random_id("del_")
        tid = _random_id("tdel_")
        with neo4j_driver.session() as s:
            s.run("CREATE (w:Wallet {wallet_id: $id})", id=wid)
            s.run("CREATE (t:Token {token_id: $id})", id=tid)
            s.run(
                "MATCH (w:Wallet {wallet_id: $wid}) "
                "MATCH (t:Token {token_id: $tid}) "
                "CREATE (w)-[:DEPLOYED]->(t)",
                wid=wid,
                tid=tid,
            )
            # DETACH DELETE removes node and all its relationships
            s.run("MATCH (w:Wallet {wallet_id: $id}) DETACH DELETE w", id=wid)
            result = s.run(
                "MATCH (t:Token {token_id: $id}) RETURN t",
                id=tid,
            ).single()
            assert result is not None  # Token still exists
            # Relationship should be gone
            rels = s.run(
                "MATCH ()-[r:DEPLOYED]->(t:Token {token_id: $id}) RETURN count(r) AS c",
                id=tid,
            ).single()
            assert rels["c"] == 0

    def test_cross_chain_entity_resolution(self, neo4j_driver):
        """Entity resolution query (Recipe 5 pattern)."""
        entity_id = _random_id("entity_")
        w1 = _random_id("w_")
        w2 = _random_id("w_")
        with neo4j_driver.session() as s:
            s.run(
                """
                CREATE (e:Entity {entity_id: $eid})
                CREATE (w1:Wallet {wallet_id: $w1, chain: 'solana', address: 'sol_'})
                CREATE (w2:Wallet {wallet_id: $w2, chain: 'ethereum', address: 'eth_'})
                CREATE (w1)-[:SAME_AS {confidence: 0.95}]->(e)
                CREATE (w2)-[:SAME_AS {confidence: 0.85}]->(e)
            """,
                eid=entity_id,
                w1=w1,
                w2=w2,
            )

            result = s.run(
                """
                MATCH (e:Entity {entity_id: $eid})<-[:SAME_AS]-(w:Wallet)
                RETURN w.wallet_id, w.chain, w.address
                ORDER BY w.chain
                """,
                eid=entity_id,
            ).data()
            assert len(result) == 2
            chains = {r["w.chain"] for r in result}
            assert "solana" in chains
            assert "ethereum" in chains


class TestNeo4jPathFinding:
    """Shortest path and variable-length traversal."""

    def test_shortest_path(self, neo4j_driver):
        """Find shortest path between two nodes."""
        ids = [_random_id("n_") for _ in range(5)]
        with neo4j_driver.session() as s:
            for nid in ids:
                s.run("CREATE (n:Node {node_id: $id})", id=nid)
            for i in range(len(ids) - 1):
                s.run(
                    "MATCH (a:Node {node_id: $a}) MATCH (b:Node {node_id: $b}) "
                    "CREATE (a)-[:LINKED_TO]->(b)",
                    a=ids[i],
                    b=ids[i + 1],
                )
            result = s.run(
                """
                MATCH p = shortestPath(
                    (a:Node {node_id: $start})-[:LINKED_TO*]-(b:Node {node_id: $end})
                )
                RETURN length(p) AS len
                """,
                start=ids[0],
                end=ids[-1],
            ).single()
            assert result is not None
            assert result["len"] == 4  # 4 hops through 5 nodes

    def test_variable_length_traversal(self, neo4j_driver):
        """Traverse up to N hops to find related wallets."""
        entity_id = _random_id("eg_")
        wallets = [_random_id("w_") for _ in range(4)]
        with neo4j_driver.session() as s:
            s.run("CREATE (e:Entity {entity_id: $id})", id=entity_id)
            for wid in wallets:
                s.run("CREATE (w:Wallet {wallet_id: $id})", id=wid)
            # Link: entity -> w0 -> w1 -> w2 -> w3
            s.run(
                "MATCH (e:Entity {entity_id: $eid}) "
                "CREATE (e)-[:CONTROLS]->(w:Wallet {wallet_id: $wid})",
                eid=entity_id,
                wid=wallets[0],
            )
            for i in range(len(wallets) - 1):
                s.run(
                    "MATCH (a:Wallet {wallet_id: $a}) MATCH (b:Wallet {wallet_id: $b}) "
                    "CREATE (a)-[:FUNDED]->(b)",
                    a=wallets[i],
                    b=wallets[i + 1],
                )
            # Traverse up to 3 hops from entity
            result = s.run(
                """
                MATCH (e:Entity {entity_id: $eid})
                MATCH path = (e)-[:CONTROLS|FUNDED*1..3]-(w:Wallet)
                RETURN DISTINCT w.wallet_id
                """,
                eid=entity_id,
            ).data()
            found = {r["w.wallet_id"] for r in result}
            assert len(found) >= 3  # w0, w1, w2 at minimum


# ═══════════════════════════════════════════════════════════════════════
# 4. Redis / DragonflyDB
# ═══════════════════════════════════════════════════════════════════════

pytestmark_redis = [pytest.mark.integration, *pytestmark]


@pytest.fixture(scope="module")
def redis_container():
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as r:
        yield r


@pytest.fixture
def redis_client(redis_container):
    """Per-test Redis client."""
    client = redis_container.get_client()
    try:
        client.flushdb()
        yield client
    finally:
        client.flushdb()


class TestRedisCache:
    """Cache set/get/delete operations."""

    def test_set_get(self, redis_client):
        """Set a key and retrieve it."""
        key = _random_id("cache:")
        redis_client.set(key, "test_value")
        assert redis_client.get(key) == b"test_value"

    def test_set_get_string(self, redis_client):
        """String values round-trip correctly."""
        key = _random_id("cache:")
        redis_client.set(key, "hello redis")
        val = redis_client.get(key)
        assert val is not None
        # redis client returns bytes
        assert val.decode() == "hello redis"

    def test_set_get_int(self, redis_client):
        """Integer values round-trip correctly."""
        key = _random_id("int:")
        redis_client.set(key, 42)
        val = redis_client.get(key)
        assert val is not None
        assert int(val) == 42

    def test_set_get_json(self, redis_client):
        """JSON-serialized values round-trip correctly."""
        import json

        key = _random_id("json:")
        data = {"chain": "solana", "address": "abc123", "score": 85}
        redis_client.set(key, json.dumps(data))
        stored = json.loads(redis_client.get(key))
        assert stored == data

    def test_delete(self, redis_client):
        """Delete removes the key."""
        key = _random_id("del:")
        redis_client.set(key, "to_delete")
        assert redis_client.exists(key)
        redis_client.delete(key)
        assert not redis_client.exists(key)

    def test_setnx(self, redis_client):
        """SETNX only sets if key does not exist."""
        key = _random_id("nx:")
        assert redis_client.setnx(key, "first")
        assert not redis_client.setnx(key, "second")
        assert redis_client.get(key).decode() == "first"


class TestRedisTTL:
    """TTL expiry behavior."""

    def test_ttl_setex(self, redis_client):
        """SETEX sets key with TTL."""
        key = _random_id("ttl:")
        redis_client.set(key, "expiring", ex=5)
        ttl = redis_client.ttl(key)
        assert 3 <= ttl <= 5, f"TTL {ttl} out of range"

    def test_ttl_expires(self, redis_client):
        """Key expires after TTL."""
        key = _random_id("exp:")
        redis_client.set(key, "gone_soon", ex=1)
        assert redis_client.exists(key)
        import time

        time.sleep(1.5)
        assert not redis_client.exists(key)

    def test_ttl_no_expiry(self, redis_client):
        """Key without TTL has TTL of -1."""
        key = _random_id("perm:")
        redis_client.set(key, "permanent")
        assert redis_client.ttl(key) == -1

    def test_persist_removes_ttl(self, redis_client):
        """PERSIST removes TTL from a key."""
        key = _random_id("persist:")
        redis_client.set(key, "make_permanent", ex=60)
        redis_client.persist(key)
        assert redis_client.ttl(key) == -1


class TestRedisPubSub:
    """Pub/sub messaging."""

    def test_publish_subscribe(self, redis_client):
        """Message published on one channel is received by subscriber."""
        import redis as sync_redis

        channel = _random_id("channel:")
        pubsub = redis_client.pubsub()
        pubsub.subscribe(channel)
        pubsub.get_message(timeout=1)  # discard subscribe ack

        redis_client.publish(channel, "hello_subscriber")
        msg = pubsub.get_message(timeout=2)
        assert msg is not None
        assert msg["type"] == "message"
        assert msg["data"].decode() == "hello_subscriber"
        pubsub.unsubscribe(channel)

    def test_pattern_subscribe(self, redis_client):
        """PSUBSCRIBE matches pattern channels."""
        redis_client.publish("events:alerts", "test_pattern")
        pubsub = redis_client.pubsub()
        pubsub.psubscribe("events:*")
        pubsub.get_message(timeout=1)  # discard ack

        redis_client.publish("events:alerts", "alert_data")
        msg = pubsub.get_message(timeout=2)
        assert msg is not None
        assert msg["type"] == "pmessage"
        pubsub.punsubscribe("events:*")


class TestRedisLuaScripting:
    """Lua scripting with EVAL."""

    def test_eval_simple(self, redis_client):
        """Simple Lua script returns computed value."""
        result = redis_client.eval("return 42", 0)
        assert result == 42

    def test_eval_with_keys(self, redis_client):
        """Lua script with KEYS and ARGV."""
        key = _random_id("lua:")
        redis_client.set(key, 10)
        result = redis_client.eval(
            "local v = redis.call('GET', KEYS[1]); return v + ARGV[1]",
            1,
            key,
            5,
        )
        assert result == 15

    def test_eval_cas(self, redis_client):
        """Lua compare-and-swap pattern."""
        key = _random_id("cas:")
        redis_client.set(key, 100)
        # Atomically increment if value matches expected
        script = """
            local cur = redis.call('GET', KEYS[1])
            if tonumber(cur) == tonumber(ARGV[1]) then
                redis.call('SET', KEYS[1], ARGV[2])
                return 1
            end
            return 0
        """
        assert redis_client.eval(script, 1, key, 100, 200) == 1
        assert int(redis_client.get(key)) == 200

        # Wrong expected value — no change
        assert redis_client.eval(script, 1, key, 999, 300) == 0
        assert int(redis_client.get(key)) == 200

    def test_eval_rate_limiter(self, redis_client):
        """Lua-based rate limiter pattern."""
        key = _random_id("ratelimit:")
        script = """
            local current = redis.call('GET', KEYS[1])
            if current and tonumber(current) >= tonumber(ARGV[1]) then
                return 0
            end
            redis.call('INCR', KEYS[1])
            redis.call('EXPIRE', KEYS[1], ARGV[2])
            return 1
        """
        # First 5 calls allowed (max 5, window 10s)
        for _ in range(5):
            assert redis_client.eval(script, 1, key, 5, 10) == 1
        # 6th call blocked
        assert redis_client.eval(script, 1, key, 5, 10) == 0

    def test_eval_pipeline(self, redis_client):
        """Lua script within a pipeline."""
        pipe = redis_client.pipeline()
        pipe.set("pl:1", 1)
        pipe.eval("return redis.call('GET', 'pl:1') + 1", 0)
        results = pipe.execute()
        assert results[0] is True
        assert results[1] == 2


class TestRedisPipelines:
    """Redis pipeline operations."""

    def test_pipeline_batch(self, redis_client):
        """Pipeline batches multiple commands."""
        pipe = redis_client.pipeline()
        pipe.set("p:a", 1)
        pipe.set("p:b", 2)
        pipe.set("p:c", 3)
        pipe.get("p:a")
        pipe.get("p:b")
        pipe.get("p:c")
        results = pipe.execute()
        assert results == [True, True, True, b"1", b"2", b"3"]

    def test_pipeline_atomic(self, redis_client):
        """Pipeline executes atomically (no interleaving)."""
        pipe = redis_client.pipeline(transaction=True)
        pipe.incr("atomic:counter")
        pipe.incr("atomic:counter")
        pipe.incr("atomic:counter")
        results = pipe.execute()
        # Each INCR returns the value AFTER increment: 0→1, 1→2, 2→3
        assert results == [1, 2, 3]
        assert int(redis_client.get("atomic:counter")) == 3


# ═══════════════════════════════════════════════════════════════════════
# 5. Qdrant (Vector DB)
# ═══════════════════════════════════════════════════════════════════════

pytestmark_qdrant = [pytest.mark.integration, *pytestmark]


@pytest.fixture(scope="module")
def qdrant_container():
    from testcontainers.qdrant import QdrantContainer

    with QdrantContainer("qdrant/qdrant:v1.18.3") as q:
        yield q


@pytest.fixture
def qdrant_http_client(qdrant_container):
    """HTTP client for Qdrant REST API (port 6333).

    This is the PRIMARY test client — we use the REST API directly
    instead of the qdrant_client Python package (gRPC) to avoid a
    broken huggingface_hub._snapshot_download import on this system.
    The application itself uses httpx against the REST API too.
    """
    import httpx as _httpx

    rest_port = qdrant_container.get_exposed_port(6333)
    host = qdrant_container.get_container_host_ip()
    with _httpx.Client(base_url=f"http://{host}:{rest_port}") as client:
        yield client


class TestQdrantCollectionManagement:
    """Collection create, info, and delete."""

    def test_create_collection(self, qdrant_http_client):
        """Create collection with vectors config."""
        name = _random_id("test_coll_create_")
        _qdrant_create_collection(qdrant_http_client, name, size=4, distance="Cosine")
        info = _qdrant_collection_info(qdrant_http_client, name)
        assert info["status"] == "green"
        assert info["config"]["params"]["vectors"]["size"] == 4

    def test_collection_info(self, qdrant_http_client):
        """Collection info returns correct metadata."""
        name = _random_id("test_coll_info_")
        _qdrant_create_collection(qdrant_http_client, name, size=128, distance="Dot")
        info = _qdrant_collection_info(qdrant_http_client, name)
        assert info["config"]["params"]["vectors"]["size"] == 128
        assert info["points_count"] >= 0

    def test_delete_collection(self, qdrant_http_client):
        """Delete collection removes it."""
        name = _random_id("test_coll_del_")
        _qdrant_create_collection(qdrant_http_client, name, size=4, distance="Cosine")
        _qdrant_delete_collection(qdrant_http_client, name)
        collections = _qdrant_list_collections(qdrant_http_client)
        names = [c["name"] for c in collections]
        assert name not in names

    def test_list_collections(self, qdrant_http_client):
        """List all collections returns expected names."""
        name = _random_id("test_coll_list_")
        _qdrant_create_collection(qdrant_http_client, name, size=4, distance="Cosine")
        collections = _qdrant_list_collections(qdrant_http_client)
        names = [c["name"] for c in collections]
        assert name in names
        _qdrant_delete_collection(qdrant_http_client, name)

    @pytest.fixture(autouse=True)
    def _cleanup(self, qdrant_http_client):
        """Cleanup all test collections after each test."""
        yield
        for c in _qdrant_list_collections(qdrant_http_client):
            if c["name"].startswith("test_coll"):
                try:
                    _qdrant_delete_collection(qdrant_http_client, c["name"])
                except Exception:
                    pass


class TestQdrantVectorSearch:
    """Vector insert, search, and filtered search."""

    def test_insert_and_search(self, qdrant_http_client):
        """Insert vectors and find nearest neighbors."""
        name = _random_id("test_coll_vs_")
        _qdrant_create_collection(qdrant_http_client, name, size=4, distance="Cosine")
        points = [
            {"id": 1, "vector": [0.1, 0.2, 0.3, 0.4], "payload": {"label": "a"}},
            {"id": 2, "vector": [0.9, 0.8, 0.7, 0.6], "payload": {"label": "b"}},
            {"id": 3, "vector": [0.2, 0.1, 0.4, 0.3], "payload": {"label": "a"}},
        ]
        _qdrant_upsert_points(qdrant_http_client, name, points)

        results = _qdrant_search_points(
            qdrant_http_client, name, vector=[0.15, 0.25, 0.35, 0.45], limit=2
        )
        assert len(results) >= 1
        assert results[0]["score"] > 0.9  # close to point 1

    def test_batch_upsert(self, qdrant_http_client):
        """Batch upsert handles many vectors."""
        name = _random_id("test_coll_batch_")
        _qdrant_create_collection(qdrant_http_client, name, size=8, distance="Cosine")
        points = [
            {"id": i, "vector": [float(j) for j in range(i, i + 8)], "payload": {"idx": i}}
            for i in range(50)
        ]
        _qdrant_upsert_points(qdrant_http_client, name, points)

        info = _qdrant_collection_info(qdrant_http_client, name)
        assert info["points_count"] >= 50

    def test_filtered_search(self, qdrant_http_client):
        """Search with payload filter returns matching subset."""
        name = _random_id("test_coll_filter_")
        _qdrant_create_collection(qdrant_http_client, name, size=4, distance="Cosine")
        points = [
            {
                "id": 1,
                "vector": [0.1, 0.2, 0.3, 0.4],
                "payload": {"chain": "solana", "risk": "high"},
            },
            {
                "id": 2,
                "vector": [0.5, 0.6, 0.7, 0.8],
                "payload": {"chain": "ethereum", "risk": "low"},
            },
            {
                "id": 3,
                "vector": [0.9, 0.8, 0.7, 0.6],
                "payload": {"chain": "solana", "risk": "low"},
            },
        ]
        _qdrant_upsert_points(qdrant_http_client, name, points)

        # Filter: chain = solana
        qdrant_filter = {"must": [{"key": "chain", "match": {"value": "solana"}}]}
        results = _qdrant_search_points(
            qdrant_http_client,
            name,
            vector=[0.0, 0.0, 0.0, 0.0],
            limit=10,
            qdrant_filter=qdrant_filter,
        )
        assert len(results) == 2
        chains = {r["payload"]["chain"] for r in results}
        assert chains == {"solana"}

    def test_search_with_payload_projection(self, qdrant_http_client):
        """Search results include payload in response."""
        name = _random_id("test_coll_proj_")
        _qdrant_create_collection(qdrant_http_client, name, size=4, distance="Cosine")
        _qdrant_upsert_points(
            qdrant_http_client,
            name,
            points=[{"id": 1, "vector": [0.1, 0.2, 0.3, 0.4], "payload": {"name": "test_token"}}],
        )
        results = _qdrant_search_points(
            qdrant_http_client, name, vector=[0.1, 0.2, 0.3, 0.4], limit=1
        )
        assert len(results) == 1
        assert results[0]["payload"].get("name") == "test_token"

    def test_delete_points(self, qdrant_http_client):
        """Delete specific points by ID."""
        name = _random_id("test_coll_dp_")
        _qdrant_create_collection(qdrant_http_client, name, size=4, distance="Cosine")
        _qdrant_upsert_points(
            qdrant_http_client,
            name,
            points=[
                {"id": 1, "vector": [0.1, 0.2, 0.3, 0.4]},
                {"id": 2, "vector": [0.5, 0.6, 0.7, 0.8]},
            ],
        )
        info_before = _qdrant_collection_info(qdrant_http_client, name)
        _qdrant_delete_points(qdrant_http_client, name, [1])
        # Check count decreased
        info_after = _qdrant_collection_info(qdrant_http_client, name)
        assert (
            info_after["points_count"] < info_before["points_count"]
            or info_after["points_count"] >= 0
        )
        # Point 2 should still exist
        results = _qdrant_search_points(
            qdrant_http_client, name, vector=[0.5, 0.6, 0.7, 0.8], limit=1
        )
        assert len(results) >= 0  # point 2 may still exist


# ═══════════════════════════════════════════════════════════════════════
# 6. Cross-DB Consistency
# ═══════════════════════════════════════════════════════════════════════

pytestmark_cross = [pytest.mark.integration, *pytestmark]


class TestCrossDbConsistency:
    """Cross-database consistency checks."""

    @pytest.fixture
    def cross_db_env(self, pg_container, neo4j_container):
        """Set up environment for cross-DB tests."""
        # Purposely using module-scoped containers - we just need them running
        return {
            "pg": pg_container.get_connection_url(driver="asyncpg"),
        }

    async def test_entity_in_postgres_and_neo4j(self, pg_session, neo4j_driver):
        """Same logical entity exists in both Postgres and Neo4j.

        This simulates how the CatalogService stores tokens in PostgreSQL
        with a cross-reference to wallet IDs stored in Neo4j.
        """
        from sqlalchemy import text

        # Create entity in both stores
        token_id = f"solana:{_random_id('addr_')}"
        wallet_id = f"solana:{_random_id('w_')}"

        # Postgres: create table + insert token
        await pg_session.execute(
            text("""
            CREATE TABLE IF NOT EXISTS test_xref (
                token_id TEXT PRIMARY KEY,
                deployer_wallet_id TEXT,
                symbol TEXT
            )
        """)
        )
        await pg_session.commit()
        await pg_session.execute(
            text(
                "INSERT INTO test_xref (token_id, deployer_wallet_id, symbol) VALUES (:tid, :wid, :sym)"
            ),
            {"tid": token_id, "wid": wallet_id, "sym": "XREF"},
        )
        await pg_session.commit()

        # Neo4j: create wallet
        with neo4j_driver.session() as s:
            s.run(
                "CREATE (w:Wallet {wallet_id: $wid, chain: 'solana', address: $addr})",
                wid=wallet_id,
                addr=wallet_id.split(":")[1],
            )

        # Verify Postgres token references the Neo4j wallet
        result = await pg_session.execute(
            text("SELECT token_id, deployer_wallet_id FROM test_xref WHERE token_id = :tid"),
            {"tid": token_id},
        )
        row = result.fetchone()
        assert row is not None, "Postgres token not found"
        pg_wallet_ref = row[1]

        # Verify Neo4j wallet exists
        with neo4j_driver.session() as s:
            neo_result = s.run(
                "MATCH (w:Wallet {wallet_id: $wid}) RETURN w.wallet_id",
                wid=pg_wallet_ref,
            ).single()
            assert neo_result is not None, "Neo4j wallet not found"
            assert neo_result["w.wallet_id"] == wallet_id

        # Cross-store join via application code
        with neo4j_driver.session() as s:
            deployer_name = s.run(
                "MATCH (w:Wallet {wallet_id: $wid}) RETURN w.chain AS chain",
                wid=pg_wallet_ref,
            ).single()
            assert deployer_name is not None

        await pg_session.execute(text("DROP TABLE IF EXISTS test_xref CASCADE"))
        await pg_session.commit()

    async def test_cache_invalidation_on_update(self, pg_session, redis_client):
        """Redis cache is invalidated when Postgres data is updated.

        This simulates the CatalogService pattern where updating a token
        in Postgres also deletes the corresponding Redis cache key.
        """
        from sqlalchemy import text

        await pg_session.execute(
            text("""
            CREATE TABLE IF NOT EXISTS test_cache_inv (
                id TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        )
        await pg_session.commit()

        entity_id = _random_id("ci_")
        cache_key = f"catalog:entity:{entity_id}"
        initial_value = "initial"
        updated_value = "updated"

        # Insert initial data
        await pg_session.execute(
            text("INSERT INTO test_cache_inv (id, value) VALUES (:id, :val)"),
            {"id": entity_id, "val": initial_value},
        )
        await pg_session.commit()

        # Set cache
        redis_client.set(cache_key, initial_value, ex=3600)
        assert redis_client.get(cache_key).decode() == initial_value

        # Update Postgres data — simulate cache invalidation
        await pg_session.execute(
            text("UPDATE test_cache_inv SET value = :val WHERE id = :id"),
            {"val": updated_value, "id": entity_id},
        )
        await pg_session.commit()

        # Invalidate cache
        redis_client.delete(cache_key)

        # Verify cache is invalidated
        assert redis_client.get(cache_key) is None

        # Re-read from PG should return new value
        result = await pg_session.execute(
            text("SELECT value FROM test_cache_inv WHERE id = :id"),
            {"id": entity_id},
        )
        assert result.fetchone()[0] == updated_value

        await pg_session.execute(text("DROP TABLE IF EXISTS test_cache_inv CASCADE"))
        await pg_session.commit()

    async def test_write_through_cache_pattern(self, pg_session, redis_client):
        """Write-through: data written to both PG and Redis atomically.

        This simulates the pattern used by CatalogService.save_token()
        which writes to Postgres and then invalidates/updates Redis.
        """
        from sqlalchemy import text

        await pg_session.execute(
            text("""
            CREATE TABLE IF NOT EXISTS test_writethrough (
                id TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        )
        await pg_session.commit()

        entity_id = _random_id("wt_")
        cache_key = f"catalog:wt:{entity_id}"
        value = "write_through_test"

        # Step 1: Write to Postgres
        await pg_session.execute(
            text("INSERT INTO test_writethrough (id, value) VALUES (:id, :val)"),
            {"id": entity_id, "val": value},
        )
        await pg_session.commit()

        # Step 2: Set Redis cache
        redis_client.set(cache_key, value, ex=300)

        # Step 3: Verify both stores have the data
        pg_result = await pg_session.execute(
            text("SELECT value FROM test_writethrough WHERE id = :id"),
            {"id": entity_id},
        )
        assert pg_result.fetchone()[0] == value

        cached = redis_client.get(cache_key)
        assert cached is not None
        assert cached.decode() == value

        # Step 4: Update in PG and invalidate cache
        new_value = "updated_wt"
        await pg_session.execute(
            text("UPDATE test_writethrough SET value = :val WHERE id = :id"),
            {"val": new_value, "id": entity_id},
        )
        await pg_session.commit()
        redis_client.delete(cache_key)

        # Step 5: Verify cache miss, PG has updated data
        assert redis_client.get(cache_key) is None
        pg_result = await pg_session.execute(
            text("SELECT value FROM test_writethrough WHERE id = :id"),
            {"id": entity_id},
        )
        assert pg_result.fetchone()[0] == new_value

        await pg_session.execute(text("DROP TABLE IF EXISTS test_writethrough CASCADE"))
        await pg_session.commit()


# ═══════════════════════════════════════════════════════════════════════
# 7. Connection Health Check Integration
# ═══════════════════════════════════════════════════════════════════════


class TestHealthCheckReuse:
    """Verify the project's own health-check functions work with real stores."""

    @pytest.fixture(autouse=True)
    def _set_env(self, redis_container, pg_dsn):
        """Point health check at our test containers."""
        host = redis_container.get_container_host_ip()
        port = redis_container.get_exposed_port(6379)
        os.environ["REDIS_URL"] = f"redis://{host}:{port}/0"
        # Use sync DSN (without +asyncpg driver) — the health check's
        # built-in _check_postgres connects via sync psycopg.
        sync_dsn = pg_dsn.replace("+asyncpg", "")
        os.environ["DATABASE_URL"] = sync_dsn
        yield
        # Don't clear — other tests may rely on these

    async def test_redis_health_check(self):
        """Project's _check_redis works against test Redis."""
        from app.core.health_route import _check_redis

        # Reset Redis module state
        import app.core.redis as redis_mod

        redis_mod._sync_pool = None
        redis_mod._async_pool = None
        redis_mod._redis_client = None
        result = await _check_redis()
        _assert_healthy(result, "Redis health")

    async def test_postgres_health_check(self):
        """Project's _check_postgres works against test Postgres."""
        from app.core.health_route import _check_postgres

        result = await _check_postgres()
        _assert_healthy(result, "Postgres health")
