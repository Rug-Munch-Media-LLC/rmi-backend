"""Regression tests for the scan-worker runtime errors (2026-10-24).

Covers the two highest-volume defects in the 24h worker log plus the Bitquery
billing loop:

* ``OSError: [Errno 9] Bad file descriptor`` — ClickHouse clients were shared
  across ``asyncio.to_thread`` workers.
* ``Future exception was never retrieved`` — cancelled ``to_thread`` work left
  its late exception unretrieved.
* ``Bitquery 402`` — a permanently failing provider was re-called every scan.
"""

from __future__ import annotations

import asyncio
import gc
import threading
import time

import pytest

from app.core.threads import run_in_thread
from app.domains.scanner.engines._ch_registry import thread_client


class _FakeClient:
    def __init__(self, key: str) -> None:
        self.key = key


def test_thread_client_is_one_per_thread() -> None:
    """Concurrent creators must not share (or close) each other's connection."""
    created: list[_FakeClient] = []

    def factory(key: str) -> _FakeClient:
        client = _FakeClient(key)
        created.append(client)
        time.sleep(0.01)
        return client

    results: dict[str, _FakeClient] = {}

    def worker(name: str) -> None:
        results[name] = thread_client("race", factory)  # type: ignore[arg-type]

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 4, "each thread must own exactly one client"
    assert len({id(client) for client in results.values()}) == 4


def test_thread_client_caches_within_a_thread() -> None:
    calls = []

    def factory(key: str) -> _FakeClient:
        calls.append(key)
        return _FakeClient(key)

    first = thread_client("cache", factory)  # type: ignore[arg-type]
    second = thread_client("cache", factory)  # type: ignore[arg-type]

    assert first is second
    assert calls == ["cache"]


def test_thread_client_does_not_cache_failures() -> None:
    """A failed connect must be retried, not memoised as unavailable forever."""
    calls = []

    def factory(key: str) -> _FakeClient:
        calls.append(key)
        return _FakeClient(key) if len(calls) > 1 else None  # type: ignore[return-value]

    assert thread_client("retry", factory) is None  # type: ignore[arg-type]
    assert thread_client("retry", factory) is not None  # type: ignore[arg-type]
    assert len(calls) == 2


async def test_cancelled_thread_work_does_not_log_never_retrieved() -> None:
    """Cancelling a to_thread caller must not leak the worker's exception."""
    loop = asyncio.get_running_loop()
    leaks: list[str] = []
    loop.set_exception_handler(lambda _loop, ctx: leaks.append(str(ctx.get("message"))))

    def boom() -> None:
        time.sleep(0.05)
        raise RuntimeError("worker failure after cancellation")

    task = asyncio.create_task(run_in_thread(boom))
    await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0.15)
    gc.collect()
    assert leaks == [], f"asyncio reported unretrieved futures: {leaks}"


async def test_run_in_thread_propagates_results_and_errors() -> None:
    assert await run_in_thread(lambda value: value * 2, 21) == 42

    def boom() -> None:
        raise ValueError("expected")

    with pytest.raises(ValueError, match="expected"):
        await run_in_thread(boom)


def test_bitquery_402_opens_cooldown() -> None:
    """A 402 stops further Bitquery calls instead of re-hitting it every scan."""
    from app.databus.bitquery_provider import BitqueryProvider

    provider = BitqueryProvider(cache=None)
    provider._loaded = True
    provider._api_key = "test-key"
    provider._oauth_token = None

    calls = {"n": 0}

    class _Response:
        status_code = 402

        def json(self) -> dict:
            return {}

        def raise_for_status(self) -> None:
            raise AssertionError("402 must not reach raise_for_status")

    class _Client:
        async def post(self, *args, **kwargs):
            calls["n"] += 1
            return _Response()

    async def _client() -> _Client:
        return _Client()

    provider._get_client = _client  # type: ignore[assignment]

    first = asyncio.run(provider._query("query { a }"))
    second = asyncio.run(provider._query("query { b }"))

    assert first == {"error": "No active billing period", "code": 402}
    assert second == {"error": "No active billing period", "code": 402}
    assert calls["n"] == 1, "cooldown must suppress the second network call"
