"""Shared plumbing for own_bundle_detect (degodding split 2026-08-13).

Holds the lazy ClickHouse client, chain normalisation, cache state, and the
query/scoring constants shared by ``own_bundle_detect.py`` (public API),
``_own_bundle_queries.py`` (CH queries), and ``_own_bundle_scoring.py``
(pure scoring). ``own_bundle_detect.py`` re-exports these names so
``bd._get_ch_client`` / ``bd._ch_client`` / ``bd._normalize_chain`` etc.
keep resolving for tests and callers.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from app.core.chain_names import normalize_chain
from app.domains.scanner.engines._logging import get_logger

# Backwards-compatible alias: callers import _normalize_chain from here.
_normalize_chain = normalize_chain

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_BUNDLE_CACHE: dict[str, dict[str, Any]] = {}
_BUNDLE_CACHE_TTL = 600  # 10 minutes
_BUNDLE_CACHE_MAX = 100

# ── Launch-window bundle detection constants ─────────────────────────────
_QUERY_TIMEOUT_SECONDS = 10  # time-box for ClickHouse queries
_LAUNCH_WINDOW_BLOCKS = 5  # first N blocks after deployment
_SIMILAR_AMOUNT_CV_BANDS = ((0.1, 100.0), (0.3, 80.0), (0.5, 50.0), (0.8, 20.0))

# ── Lazy ClickHouse client ────────────────────────────────────────────────
_ch_client: Any = None

# ── Chain normalization ───────────────────────────────────────────────────




def _new_ch_client() -> Any | None:
    """Dedicated ClickHouse connection for concurrent query paths.

    The shared singleton rejects simultaneous queries; callers that run
    multiple ``_query_*`` helpers concurrently (asyncio.gather via
    ``run_sync``) must hand each one its own connection — same pattern as
    ``_io._compute_wallet_metrics``.
    """
    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    try:
        from clickhouse_driver import Client as CHClient

        return CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database="wallet_memory",
            connect_timeout=3,
            send_receive_timeout=15,
        )
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own bundle detect dedicated ClickHouse client unavailable: %s", e)
        return None


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native protocol client."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        _ch_client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=15,
        )
        _ch_client.execute("SELECT 1")
        logger.info("Own bundle detect ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own bundle detect ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


# ── Analysis budget (2026-09-23) ──────────────────────────────────────────
# The unified scan phase helper used to gather the two detectors with NO
# timeout. On Base each ran ~47s, eating ~100s of the 120s scan budget and
# capping worker throughput at ~1 token/min (scan queue drained slower than
# discovery filled it). Hard-bounded here; on timeout both results degrade
# to a TimeoutError for the caller's existing error handling.
BUNDLE_BUDGET_S = float(os.getenv("BUNDLE_BUDGET_S", "15"))


async def bounded_pair(*coros: Any) -> tuple[Any, ...]:
    """Await every coro under BUNDLE_BUDGET_S; cancel and degrade on timeout."""
    try:
        return tuple(await asyncio.wait_for(asyncio.gather(*coros, return_exceptions=True), timeout=BUNDLE_BUDGET_S))
    except TimeoutError:
        return tuple(TimeoutError(f"bundle analysis exceeded {BUNDLE_BUDGET_S:.0f}s budget") for _ in coros)
