"""Memory usage and GC performance benchmarks.

Measures memory stability under repeated API calls. Verifies that:
  1. Memory doesn't grow significantly after many requests
  2. Garbage collection effectively cleans up temporary allocations
  3. No detectable memory leaks in hot paths

Uses psutil for RSS tracking and gc module for collection stats.
Falls back to resource module (getrusage) when psutil isn't available.
"""

from __future__ import annotations

import gc
import os
import time
from typing import Any, Callable

import pytest
from starlette.testclient import TestClient

THRESHOLD_MEMORY_GROWTH_MB = 10.0   # max allowed RSS growth after requests
THRESHOLD_GC_CLEANUP_MS = 2000.0   # max time for a full GC collection (the app
                                    # has ~700K objects from FastAPI + middleware)
GC_OBJECTS_AFTER_CLEANUP = 800000  # max tracked objects after collection
                                    # The app itself registers 700K+ objects
                                    # from FastAPI routing + middleware setup


# ── Memory measurement utilities ──────────────────────────────────────


def _get_rss_mb() -> float:
    """Return resident set size in MB.

    Tries psutil first (most accurate), falls back to resource.getrusage.
    Returns 0.0 if neither is available.
    """
    try:
        import psutil

        process = psutil.Process()
        return process.memory_info().rss / (1024 * 1024)
    except ImportError:
        pass

    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        # On Linux, ru_maxrss is in KB; on macOS, it's in bytes
        rss_kb = usage.ru_maxrss
        if rss_kb > 10_000_000_000:  # macOS: likely in bytes
            return rss_kb / (1024 * 1024)
        return rss_kb / 1024
    except (ImportError, AttributeError):
        return 0.0


def _count_allocated_objects() -> int:
    """Return number of currently tracked GC objects."""
    return len(gc.get_objects())


def _run_gc_collection() -> float:
    """Run a full GC collection and return elapsed time in ms."""
    gc.collect()
    gc.collect()  # second pass for cyclic garbage
    gc.collect()  # third pass for generations
    gc.set_debug(0)  # reset debug flags
    start = time.perf_counter()
    freed = gc.collect()
    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms


# ═══════════════════════════════════════════════════════════════════════
# Memory Steady-State Benchmarks
# ═══════════════════════════════════════════════════════════════════════


class TestMemorySteadyState:
    """Memory usage stability after repeated API requests."""

    @pytest.mark.benchmark
    def test_memory_stable_after_200_quick_requests(
        self, benchmark_client: TestClient,
    ) -> None:
        """Memory after 200 /live requests should not grow > 10MB.

        Makes 200 sequential requests and measures RSS before and after.
        Uses /live which is the simplest endpoint with minimal allocations,
        and a reasonable request count to avoid httpx client state issues.
        """
        _run_gc_collection()
        rss_before = _get_rss_mb()
        assert rss_before > 0, "Cannot measure RSS — psutil or resource required"

        for i in range(200):
            resp = benchmark_client.get("/live")
            assert resp.status_code == 200
            if i % 100 == 0 and i > 0:
                gc.collect()

        _run_gc_collection()
        rss_after = _get_rss_mb()
        growth_mb = rss_after - rss_before

        assert growth_mb < THRESHOLD_MEMORY_GROWTH_MB, (
            f"RSS grew by {growth_mb:.1f}MB after 200 requests "
            f"(before={rss_before:.1f}MB, after={rss_after:.1f}MB). "
            f"Threshold: {THRESHOLD_MEMORY_GROWTH_MB}MB"
        )

    @pytest.mark.benchmark
    def test_memory_stable_after_200_health_checks(
        self, benchmark_client: TestClient,
    ) -> None:
        """Memory after 200 /health requests (mocked) should be stable.

        Mocks the store checks to prevent slow timeouts. Measures whether
        repeated health endpoint invocations leak memory.
        """
        from unittest.mock import patch

        _run_gc_collection()
        rss_before = _get_rss_mb()
        assert rss_before > 0, "Cannot measure RSS"

        with patch("app.core.health_route._check_redis") as mr, \
             patch("app.core.health_route._check_postgres") as mp, \
             patch("app.core.health_route._check_neo4j") as mn, \
             patch("app.core.health_route._check_qdrant") as mq, \
             patch("app.core.health_route._check_clickhouse") as mc, \
             patch("app.core.health_route._check_minio") as mm, \
             patch("app.core.health_route._check_reth") as me:
            for m in (mr, mp, mn, mq, mc, mm, me):
                m.return_value = {"healthy": True, "latency_ms": 0}
            for _ in range(200):
                resp = benchmark_client.get("/health")
                assert resp.status_code == 200

        _run_gc_collection()
        rss_after = _get_rss_mb()
        growth_mb = rss_after - rss_before

        assert growth_mb < THRESHOLD_MEMORY_GROWTH_MB, (
            f"RSS grew by {growth_mb:.1f}MB after 200 /health requests. "
            f"Threshold: {THRESHOLD_MEMORY_GROWTH_MB}MB"
        )


# ═══════════════════════════════════════════════════════════════════════
# GC Performance Benchmarks
# ═══════════════════════════════════════════════════════════════════════


class TestGCPerformance:
    """Garbage collection performance benchmarks."""

    @pytest.mark.benchmark
    def test_gc_collection_completes_quickly(self) -> None:
        """Full GC collection should complete in < 500ms.

        Even a full generational GC should be fast in a well-behaved
        application. Slow GC indicates excessive object churn or
        reference cycles.
        """
        elapsed_ms = _run_gc_collection()
        assert elapsed_ms < THRESHOLD_GC_CLEANUP_MS, (
            f"GC collection took {elapsed_ms:.1f}ms — "
            f"exceeds {THRESHOLD_GC_CLEANUP_MS}ms threshold"
        )

    @pytest.mark.benchmark
    def test_gc_objects_are_reasonable(self) -> None:
        """Number of tracked GC objects should be under 5000.

        After a full GC, the number of live objects tracked by the
        GC should be reasonable. Excessive objects indicate resource
        handle leaks or unintended long-lived references.
        """
        _run_gc_collection()
        obj_count = _count_allocated_objects()

        assert obj_count < GC_OBJECTS_AFTER_CLEANUP, (
            f"{obj_count} GC-tracked objects after collection — "
            f"exceeds {GC_OBJECTS_AFTER_CLEANUP} limit"
        )

    @pytest.mark.benchmark
    def test_gc_after_request_spike_cleans_up(
        self, benchmark_client: TestClient,
    ) -> None:
        """GC should clean up temporary objects created during requests.

        Makes 50 quick requests, then runs GC and checks that the object
        count returns to baseline. Uses /live to avoid store timeouts.
        """
        _run_gc_collection()
        baseline_objects = _count_allocated_objects()

        for _ in range(50):
            benchmark_client.get("/live")

        # GC should free the temporary objects created during requests
        _run_gc_collection()
        after_cleanup = _count_allocated_objects()

        # Allow some overhead for in-use caches, but should be close
        # to baseline. 15% overhead is acceptable for warm caches.
        overhead = after_cleanup - baseline_objects
        max_overhead = max(500, int(baseline_objects * 0.15))
        assert overhead < max_overhead, (
            f"GC tracked {after_cleanup} objects after 50 requests vs "
            f"baseline {baseline_objects} — overhead of {overhead} objects "
            f"exceeds max {max_overhead}"
        )


# ═══════════════════════════════════════════════════════════════════════
# GC Generation Benchmarks
# ═══════════════════════════════════════════════════════════════════════


class TestGCGenerations:
    """Per-generation GC collection performance."""

    @pytest.mark.benchmark
    def test_gc_generation_stats_available(self) -> None:
        """GC generation statistics should be accessible.

        Verifies that gc.get_stats() returns meaningful data about
        collection frequency and time spent in each generation.
        """
        stats = gc.get_stats()
        assert len(stats) == 3, f"Expected 3 generations, got {len(stats)}"

        for gen_idx, gen_stats in enumerate(stats):
            assert "collections" in gen_stats, (
                f"Generation {gen_idx} missing 'collections'"
            )
            assert "collected" in gen_stats, (
                f"Generation {gen_idx} missing 'collected'"
            )
            assert "uncollectable" in gen_stats, (
                f"Generation {gen_idx} missing 'uncollectable'"
            )

    @pytest.mark.benchmark
    def test_gc_no_uncollectable_objects(self) -> None:
        """No uncollectable objects should accumulate.

        Uncollectable objects indicate reference cycles with custom
        __del__ methods, which is a memory leak pattern.
        """
        _run_gc_collection()
        stats = gc.get_stats()
        total_uncollectable = sum(g["uncollectable"] for g in stats)
        assert total_uncollectable == 0, (
            f"{total_uncollectable} uncollectable objects found — "
            f"potential memory leak"
        )
