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

THRESHOLD_MEMORY_GROWTH_MB = 10.0   # max allowed RSS growth after 1000 requests
THRESHOLD_GC_CLEANUP_MS = 500.0     # max time for a full GC collection
GC_OBJECTS_AFTER_CLEANUP = 5000     # max unfreed objects after GC


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
    def test_memory_stable_after_1000_health_checks(
        self, benchmark_client: TestClient,
    ) -> None:
        """Memory after 1000 /health requests should not grow > 10MB.

        Makes 1000 sequential health check requests and measures RSS
        before and after. A significant increase suggests a memory leak
        in the request handling path.
        """
        _run_gc_collection()
        rss_before = _get_rss_mb()
        assert rss_before > 0, "Cannot measure RSS — psutil or resource required"

        for i in range(1000):
            resp = benchmark_client.get("/health")
            assert resp.status_code == 200
            if i % 200 == 0 and i > 0:
                # Periodic GC to prevent cross-test contamination
                gc.collect()

        _run_gc_collection()
        rss_after = _get_rss_mb()
        growth_mb = rss_after - rss_before

        assert growth_mb < THRESHOLD_MEMORY_GROWTH_MB, (
            f"RSS grew by {growth_mb:.1f}MB after 1000 requests "
            f"(before={rss_before:.1f}MB, after={rss_after:.1f}MB). "
            f"Threshold: {THRESHOLD_MEMORY_GROWTH_MB}MB"
        )

    @pytest.mark.benchmark
    def test_memory_stable_after_500_live_checks(
        self, benchmark_client: TestClient,
    ) -> None:
        """Memory after 500 /live requests should be stable.

        /live is the simplest endpoint (returns 'alive' immediately)
        and should have zero allocation overhead.
        """
        _run_gc_collection()
        rss_before = _get_rss_mb()
        assert rss_before > 0, "Cannot measure RSS"

        for _ in range(500):
            resp = benchmark_client.get("/live")
            assert resp.status_code == 200

        _run_gc_collection()
        rss_after = _get_rss_mb()
        growth_mb = rss_after - rss_before

        assert growth_mb < 5.0, (
            f"RSS grew by {growth_mb:.1f}MB after 500 /live requests. "
            f"Threshold: 5MB"
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

        Makes 200 requests, then runs GC and checks that the object
        count returns to baseline.
        """
        _run_gc_collection()
        baseline_objects = _count_allocated_objects()

        for _ in range(200):
            benchmark_client.get("/health")

        # GC should free the temporary objects created during requests
        _run_gc_collection()
        after_cleanup = _count_allocated_objects()

        # Allow some overhead for in-use caches, but should be close
        # to baseline. 10% overhead is acceptable.
        overhead = after_cleanup - baseline_objects
        max_overhead = max(500, int(baseline_objects * 0.10))
        assert overhead < max_overhead, (
            f"GC tracked {after_cleanup} objects after 200 requests vs "
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
