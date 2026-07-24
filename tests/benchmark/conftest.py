"""Shared fixtures for benchmark tests.

Sets required env vars, provides timing utilities, and creates
the FastAPI app with TestClient for API benchmarks.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncGenerator, Generator
from typing import Any, Protocol

import pytest
from typing_extensions import runtime_checkable

# ── Required env vars for app factory boot ─────────────────────────────
os.environ.setdefault("HF_HUB_DISABLE_IMPORT", "1")
os.environ.setdefault("RMI_AUTH_TOKEN", "test-auth-token-32-chars-long!!")
os.environ.setdefault("RMI_INTERNAL_KEY", "test-internal-key-32-chars-long!!!")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-key-thats-32-chars-long!")
os.environ.setdefault("RMI_JWT_SECRET", "test-jwt-secret-key-thats-32-chars-long!")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://rmi:rmi@localhost:5432/rmi")
os.environ.setdefault("CLICKHOUSE_HOST", "localhost")
os.environ.setdefault("CLICKHOUSE_PORT", "9000")


# ── Timer Utility ──────────────────────────────────────────────────────


class Timer:
    """High-resolution timer for benchmark measurements.

    Usage:
        timer = Timer()
        with timer.measure("operation"):
            do_something()
        print(f"{timer.elapsed_ms():.1f}ms")
    """

    def __init__(self) -> None:
        self._start: float = 0.0
        self._elapsed: float = 0.0
        self._measurements: dict[str, list[float]] = {}

    def start(self) -> None:
        """Start the timer."""
        self._start = time.perf_counter()

    def stop(self) -> float:
        """Stop the timer and return elapsed seconds."""
        self._elapsed = time.perf_counter() - self._start
        return self._elapsed

    def elapsed_ms(self) -> float:
        """Return elapsed time in milliseconds."""
        return self._elapsed * 1000

    def measure(self, name: str = "") -> "TimerContext":
        """Context manager that records elapsed time."""
        return TimerContext(self, name)

    @property
    def measurements(self) -> dict[str, list[float]]:
        return self._measurements


class TimerContext:
    """Context manager for timing code blocks."""

    def __init__(self, timer: Timer, name: str = "") -> None:
        self._timer = timer
        self._name = name
        self._start: float = 0.0

    def __enter__(self) -> "TimerContext":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        elapsed = time.perf_counter() - self._start
        if self._name:
            self._timer._measurements.setdefault(self._name, []).append(elapsed * 1000)


@pytest.fixture
def timer() -> Timer:
    """Returns a fresh Timer for each benchmark test."""
    return Timer()


# ── Result assertion helpers ───────────────────────────────────────────


@pytest.fixture
def assert_time() -> Callable[[float, float, str], None]:
    """Returns a function that asserts elapsed time is below threshold."""
    import pytest

    def _assert_time(elapsed_ms: float, threshold_ms: float, operation: str = "") -> None:
        """Assert elapsed_ms < threshold_ms. Shows drift as percentage."""
        if elapsed_ms > threshold_ms:
            drift_pct = ((elapsed_ms - threshold_ms) / threshold_ms) * 100
            op_msg = f" [{operation}]" if operation else ""
            pytest.fail(
                f"Benchmark exceeded threshold{op_msg}: "
                f"{elapsed_ms:.1f}ms > {threshold_ms:.1f}ms "
                f"(drift: +{drift_pct:.1f}%)"
            )

    return _assert_time


# ── FastAPI app fixtures ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def benchmark_app():
    """Build the FastAPI app once per module for benchmarking."""
    from app.factory import create_app

    return create_app()


@pytest.fixture
def benchmark_client(benchmark_app):
    """TestClient against the real app for each test.

    Sends a warmup request so subsequent benchmark measurements
    reflect warm-state performance (cold imports and async init
    happen during warmup, not during measurement).
    """
    from starlette.testclient import TestClient

    client = TestClient(benchmark_app)
    # Warmup: send a request to trigger all lazy imports + middleware init
    client.get("/live")
    return client


# ── Database connection helpers (skip if unavailable) ──────────────────


@pytest.fixture
def redis_conn():
    """Redis connection — skip benchmark if Redis is unavailable."""
    redis = pytest.importorskip("redis")
    try:
        client = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            db=0,
            socket_connect_timeout=2,
        )
        client.ping()
        yield client
        client.close()
    except redis.exceptions.ConnectionError:
        pytest.skip("Redis not available — benchmark skipped")
    except Exception:
        pytest.skip("Redis not available — benchmark skipped")


@pytest.fixture
async def async_redis_conn():
    """Async Redis connection — skip benchmark if Redis is unavailable."""
    redis = pytest.importorskip("redis.asyncio")
    try:
        client = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            db=0,
            socket_connect_timeout=2,
        )
        await client.ping()
        yield client
        await client.aclose()
    except (redis.exceptions.ConnectionError, OSError):
        pytest.skip("Redis not available — benchmark skipped")
    except Exception:
        pytest.skip("Redis not available — benchmark skipped")


@pytest.fixture
async def pg_conn():
    """asyncpg connection — skip benchmark if PostgreSQL is unavailable."""
    asyncpg = pytest.importorskip("asyncpg")
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://rmi:rmi@localhost:5432/rmi",
    )
    # Convert asyncpg URL removing +asyncpg prefix
    clean_url = db_url.replace("+asyncpg", "")
    try:
        conn = await asyncpg.connect(clean_url, timeout=3)
        yield conn
        await conn.close()
    except (asyncpg.exceptions.CannotConnectNowError, OSError, ConnectionRefusedError):
        pytest.skip("PostgreSQL not available — benchmark skipped")
    except Exception:
        pytest.skip("PostgreSQL not available — benchmark skipped")


@pytest.fixture
async def ch_conn():
    """ClickHouse native protocol connection — skip if unavailable."""
    clickhouse_driver = pytest.importorskip("clickhouse_driver")
    host = os.environ.get("CLICKHOUSE_HOST", "localhost")
    port = int(os.environ.get("CLICKHOUSE_PORT", "9000"))
    try:
        from clickhouse_driver import Client as CHClient

        client = CHClient(
            host=host,
            port=port,
            user=os.environ.get("CLICKHOUSE_USER", "rmi"),
            password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
            connect_timeout=3,
        )
        client.execute("SELECT 1")
        yield client
        client.disconnect()
    except (ImportError, OSError, ConnectionRefusedError):
        pytest.skip("ClickHouse not available — benchmark skipped")
    except Exception:
        pytest.skip("ClickHouse not available — benchmark skipped")


# ── Callable type for assert_time fixture ──────────────────────────────
from collections.abc import Callable
