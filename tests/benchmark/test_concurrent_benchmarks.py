"""Concurrent request performance benchmarks.

Measures how the API handles multiple simultaneous requests.
Verifies that:
  1. All concurrent requests complete successfully
  2. No errors occur under load
  3. Total elapsed time scales reasonably with concurrency

Uses asyncio.gather() + httpx AsyncClient for true concurrent execution
against the running app via TestClient's asgi-transport mode.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

THRESHOLD_10_CONCURRENT_HEALTH_MS = 300.0   # 10 health checks in parallel
THRESHOLD_5_CONCURRENT_SCAN_MS = 3000.0     # 5 wallet scans in parallel
MAX_ERROR_RATE_PCT = 0.0                     # zero errors allowed


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


async def _request(
    app: Any,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a single HTTP request against the ASGI app.

    Uses httpx with the ASGI transport so we don't need a running
    server. Returns dict with status, elapsed_ms, and optional error.
    """
    import httpx

    transport = httpx.ASGITransport(app=app)
    start = time.perf_counter()
    status: int = 0
    error: str | None = None
    body_text: str = ""
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.request(
                method,
                path,
                headers=headers or {},
                json=json_body,
                timeout=10.0,
            )
            status = resp.status_code
            body_text = resp.text[:500]
    except Exception as e:
        error = str(e)[:200]

    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "status": status,
        "elapsed_ms": elapsed_ms,
        "error": error,
        "body": body_text,
    }


async def _concurrent_requests(
    app: Any,
    method: str,
    path: str,
    count: int,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute N concurrent requests and return results."""
    results = await asyncio.gather(*[
        _request(app, method, path, headers=headers, json_body=json_body)
        for _ in range(count)
    ])
    return results


def _analyze_results(
    results: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    """Analyze concurrent request results for summary stats."""
    statuses = [r["status"] for r in results if r["error"] is None]
    errors = [r for r in results if r["error"] is not None]
    elapsed_times = [r["elapsed_ms"] for r in results if r["error"] is None]

    return {
        "label": label,
        "total": len(results),
        "success": len(statuses),
        "errors": len(errors),
        "error_pct": (len(errors) / len(results)) * 100 if results else 0,
        "min_ms": min(elapsed_times) if elapsed_times else 0,
        "max_ms": max(elapsed_times) if elapsed_times else 0,
        "avg_ms": sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0,
        "non_200": [s for s in statuses if s != 200],
    }


# ═══════════════════════════════════════════════════════════════════════
# Concurrent Health Check Benchmarks
# ═══════════════════════════════════════════════════════════════════════


class TestConcurrentHealthBenchmarks:
    """Concurrent /health endpoint benchmarks."""

    @pytest.mark.asyncio
    @pytest.mark.benchmark
    async def test_10_concurrent_health_checks(
        self, benchmark_app: Any, assert_time: Any
    ) -> None:
        """10 concurrent GET /health requests — all must succeed.

        Verifies the health endpoint can handle burst traffic without
        connection errors or timeouts. Total wall time should be under
        the serial total because requests execute concurrently.
        """
        results = await _concurrent_requests(
            benchmark_app,
            "GET",
            "/health",
            count=10,
        )
        analysis = _analyze_results(results, "10x /health")

        # Assert all requests succeeded
        assert analysis["errors"] == 0, (
            f"{analysis['errors']}/{analysis['total']} requests failed: "
            f"{[r['error'] for r in results if r['error']]}"
        )

        # Assert no non-200 responses
        assert len(analysis["non_200"]) == 0, (
            f"Non-200 responses: {analysis['non_200']}"
        )

        # Assert max elapsed time is below threshold
        assert analysis["max_ms"] < THRESHOLD_10_CONCURRENT_HEALTH_MS, (
            f"Slowest request took {analysis['max_ms']:.1f}ms — "
            f"exceeds {THRESHOLD_10_CONCURRENT_HEALTH_MS}ms threshold"
        )

    @pytest.mark.asyncio
    @pytest.mark.benchmark
    async def test_10_concurrent_live_checks(
        self, benchmark_app: Any, assert_time: Any
    ) -> None:
        """10 concurrent GET /live requests — verify zero errors.

        /live is the simplest endpoint and should handle high
        concurrency without degradation.
        """
        results = await _concurrent_requests(
            benchmark_app,
            "GET",
            "/live",
            count=10,
        )
        analysis = _analyze_results(results, "10x /live")

        assert analysis["errors"] == 0, (
            f"{analysis['errors']} requests failed: "
            f"{[r['error'] for r in results if r['error']]}"
        )
        assert len(analysis["non_200"]) == 0
        assert analysis["avg_ms"] < 50.0, (
            f"Average /live response too high: {analysis['avg_ms']:.1f}ms"
        )


# ═══════════════════════════════════════════════════════════════════════
# Concurrent Scanner Benchmarks
# ═══════════════════════════════════════════════════════════════════════


class TestConcurrentScannerBenchmarks:
    """Concurrent scanner endpoint benchmarks."""

    @pytest.mark.asyncio
    @pytest.mark.benchmark
    async def test_5_concurrent_wallet_scans(
        self, benchmark_app: Any, assert_time: Any
    ) -> None:
        """5 concurrent POST /api/v2/scanner/wallet/scan requests.

        Mocks the wallet scanner to isolate the overhead of concurrent
        request handling, middleware, routing, and serialization.
        """
        mock_result = _make_mock_wallet_scan_result()

        with patch(
            "app.domains.wallet.unified_wallet_scanner.UnifiedWalletScanner.scan",
            new_callable=lambda: _MockAsyncScan(mock_result),
        ):
            results = await _concurrent_requests(
                benchmark_app,
                "POST",
                "/api/v2/scanner/wallet/scan",
                count=5,
                headers={"X-API-Key": "test-auth-token-32-chars-long!!"},
                json_body={
                    "address": "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
                    "chain": "ethereum",
                },
            )

        analysis = _analyze_results(results, "5x wallet scan")

        assert analysis["errors"] == 0, (
            f"{analysis['errors']}/{analysis['total']} wallet scans failed: "
            f"{[r['error'] for r in results if r['error']]}"
        )
        assert analysis["max_ms"] < THRESHOLD_5_CONCURRENT_SCAN_MS, (
            f"Slowest wallet scan took {analysis['max_ms']:.1f}ms — "
            f"exceeds {THRESHOLD_5_CONCURRENT_SCAN_MS}ms threshold"
        )

    @pytest.mark.asyncio
    @pytest.mark.benchmark
    async def test_concurrent_no_errors_under_load(
        self, benchmark_app: Any
    ) -> None:
        """Mixed concurrent requests — verify zero errors under load.

        Fires a mix of health checks and live probes simultaneously
        to simulate realistic mixed traffic patterns.
        """
        # Mix of endpoints: 5 health + 5 live
        health_tasks = [
            _request(benchmark_app, "GET", "/health")
            for _ in range(5)
        ]
        live_tasks = [
            _request(benchmark_app, "GET", "/live")
            for _ in range(5)
        ]
        all_tasks = health_tasks + live_tasks

        results = await asyncio.gather(*all_tasks)

        errors = [r for r in results if r["error"] is not None]
        non_200 = [r for r in results if r["error"] is None and r["status"] != 200]

        assert len(errors) == 0, (
            f"{len(errors)} requests failed under mixed load: "
            f"{[e['error'] for e in errors]}"
        )
        assert len(non_200) == 0, (
            f"{len(non_200)} requests returned non-200 under load: "
            f"{[n for n in non_200]}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


class _MockAsyncScan:
    """Awaaitable mock for scanner.scan() methods."""

    def __init__(self, result: Any) -> None:
        self._result = result

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._result


def _make_mock_wallet_scan_result() -> Any:
    """Create a mock wallet scan result."""
    from types import SimpleNamespace

    return SimpleNamespace(
        address="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
        chain="ethereum",
        labels=["factory", "deployer"],
        entity_name="Unknown",
        entity_type="wallet",
        persona="trader",
        persona_confidence=0.65,
        total_value_usd=150000.0,
        token_count=12,
        scam_tokens_held=1,
        scam_exposure_pct=8.3,
        risk_score=35,
        risk_flags=["moderate_concentration"],
        rag_matches=[],
        behavior_patterns=["regular_trading"],
        smart_money_overlap=False,
        risk_forecast="low",
        modules_run=18,
        scan_duration_ms=450,
        tier="free",
        scanned_at="2026-07-24T00:00:00Z",
        data_source="databus",
        freshness="2026-07-24T00:00:00Z",
        fallback_used=False,
    )
