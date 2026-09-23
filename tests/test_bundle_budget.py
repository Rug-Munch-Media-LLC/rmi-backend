"""Bundle-analysis budget: detectors must never outlive BUNDLE_BUDGET_S (2026-09-23)."""

import asyncio
import time

from app.domains.scanner.engines import _own_bundle_common as common


async def _echo(value):
    return {"result": value}


async def _slow():
    await asyncio.sleep(5)
    return {"result": "too-slow"}


def test_bounded_pair_returns_fast_results() -> None:
    bundle, same_slot = asyncio.run(common.bounded_pair(_echo("bundle"), _echo("same_slot")))
    assert bundle == {"result": "bundle"}
    assert same_slot == {"result": "same_slot"}


def test_bounded_pair_times_out_slow_tasks(monkeypatch) -> None:
    monkeypatch.setattr(common, "BUNDLE_BUDGET_S", 0.05)
    started = time.monotonic()
    first, second = asyncio.run(common.bounded_pair(_slow(), _slow()))
    assert time.monotonic() - started < 2.0
    assert isinstance(first, TimeoutError)
    assert isinstance(second, TimeoutError)
