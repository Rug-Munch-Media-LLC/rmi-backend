"""L1Cache (SWR in-memory layer) contracts — previously untested.

* fresh hit → (value, False); stale-window hit → (value, True)
* past stale window → evicted, (None, False)
* LRU eviction at max_keys; delete/clear; stats counters
"""

from __future__ import annotations

import time

import pytest

from app.databus.cache import L1Cache


@pytest.mark.asyncio
async def test_fresh_hit():
    c = L1Cache()
    await c.set("k", {"v": 1}, ttl=60)
    val, stale = await c.get("k")
    assert val == {"v": 1}
    assert stale is False


@pytest.mark.asyncio
async def test_stale_window_swr_hit():
    c = L1Cache(stale_multiplier=2.5)
    await c.set("k", "val", ttl=10)
    # simulate fresh expiry but within stale window
    stored = c._store["k"]
    _, _fresh, stale_exp = stored
    c._store["k"] = ("val", time.monotonic() - 1, stale_exp)
    val, is_stale = await c.get("k")
    assert val == "val"
    assert is_stale is True
    assert c.stale_hits == 1


@pytest.mark.asyncio
async def test_past_stale_window_evicted():
    c = L1Cache()
    await c.set("k", "val", ttl=10)
    entry = c._store["k"]
    c._store["k"] = ("val", time.monotonic() - 100, time.monotonic() - 50)
    del entry
    val, stale = await c.get("k")
    assert val is None
    assert stale is False
    assert "k" not in c._store  # evicted


@pytest.mark.asyncio
async def test_miss_counter():
    c = L1Cache()
    await c.get("never-set")
    assert c.misses == 1


@pytest.mark.asyncio
async def test_lru_eviction_at_max():
    c = L1Cache(max_keys=3)
    for i in range(4):
        await c.set(f"k{i}", i, ttl=60)
    assert len(c._store) == 3
    val0, _ = await c.get("k0")
    assert val0 is None  # oldest evicted
    val3, _ = await c.get("k3")
    assert val3 == 3


@pytest.mark.asyncio
async def test_delete_and_clear():
    c = L1Cache()
    await c.set("a", 1, ttl=60)
    await c.set("b", 2, ttl=60)
    await c.delete("a")
    got_a, _ = await c.get("a")
    assert got_a is None
    await c.clear()
    got_b, _ = await c.get("b")
    assert got_b is None


@pytest.mark.asyncio
async def test_stats_shape():
    c = L1Cache()
    await c.set("k", 1, ttl=60)
    await c.get("k")
    await c.get("missing")
    st = c.stats()
    assert st["hits"] == 1
    assert st["misses"] == 1
    assert st["hit_rate"] == 50.0
    assert "stale_hits" in st
    assert "fresh_hit_rate" in st
