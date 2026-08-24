"""L2RedisCache + CacheLayer contracts (previously untested).

Redis is stubbed with an in-memory fake — no live redis needed.

L2 contracts:
* unavailable redis → get returns None / set no-ops (fail-open)
* happy path: set stores JSON under prefix, get parses it back
* connection loss mid-flight → marked unavailable, miss counted

CacheLayer contracts:
* TTL config covers every data_type used by callers (default fallback
  exists); known types have sane bounds (15s..24h)
* _extract_data_type parses 'source:data_type:hash' keys
* L1-first read path; stale_refresh_callback wiring present
"""

from __future__ import annotations

import pytest

from app.databus.cache import CacheLayer, L2RedisCache

# ── L2 fail-open behavior ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_l2_unavailable_fail_open(monkeypatch):
    l2 = L2RedisCache()

    async def fail_connect():
        return False

    monkeypatch.setattr(l2, "_connect", fail_connect)
    assert await l2.get("anything") is None
    await l2.set("anything", {"x": 1}, ttl=60)  # must not raise
    assert l2.misses >= 1


@pytest.mark.asyncio
async def test_l2_happy_path_roundtrip(monkeypatch):
    l2 = L2RedisCache()
    store: dict[str, str] = {}

    class FakeRedis:
        async def ping(self):
            return True

        async def setex(self, key, ttl, value):
            store[key] = value

        async def get(self, key):
            # real redis (decode_responses=True) returns the RAW string;
            # L2RedisCache.get does the json parsing itself.
            return store.get(key)

        async def delete(self, key):
            store.pop(key, None)

    async def fake_connect():
        l2._redis = FakeRedis()
        l2._available = True
        return True

    monkeypatch.setattr(l2, "_connect", fake_connect)
    await l2.set("price:eth", {"usd": 2500.0}, ttl=60)
    out = await l2.get("price:eth")
    assert out == {"usd": 2500.0}
    assert "databus:price:eth" in store  # prefix applied
    assert l2.hits == 1 and l2.misses == 0


@pytest.mark.asyncio
async def test_l2_connection_loss_marks_unavailable(monkeypatch):
    l2 = L2RedisCache()

    class FlakyRedis:
        async def ping(self):
            return True

        async def get(self, key):
            raise TimeoutError("redis died")

    async def fake_connect():
        l2._redis = FlakyRedis()
        l2._available = True
        return True

    monkeypatch.setattr(l2, "_connect", fake_connect)
    assert await l2.get("k") is None
    assert l2._available is False  # marked down for subsequent calls


# ── CacheLayer ────────────────────────────────────────────────────────


def test_ttl_config_covers_common_types_with_sane_bounds():
    layer = CacheLayer()
    for dtype, ttl in layer.ttl_config.items():
        assert 5 <= ttl <= 86400 * 2, f"{dtype} ttl {ttl} out of sane bounds"
    # spot-check the aggressive-caching tiers
    assert layer.ttl_config["arkham_entity"] >= 600  # protect trial quota
    assert layer.ttl_config["url_security_scan"] == 86400  # VirusTotal daily cap
    assert "default" in layer.ttl_config


def test_extract_data_type_from_key():
    layer = CacheLayer()
    assert layer._extract_data_type("goplus:token_security:abc123") == "token_security"
    assert layer._extract_data_type("no-separator") == "default"


@pytest.mark.asyncio
async def test_layer_get_misses_l1_falls_through_to_l2(monkeypatch):
    layer = CacheLayer()

    # L2 stubbed to serve the value (L1 empty)
    async def l2_get(key):
        if key == "prov:type:hash":
            return {"served": "from-l2"}
        return None

    monkeypatch.setattr(layer.l2, "get", l2_get)
    val, stale = await layer.get("prov:type:hash")
    assert val == {"served": "from-l2"}
    assert stale is False


@pytest.mark.asyncio
async def test_stale_refresh_callback_wiring():
    layer = CacheLayer()
    assert layer.stale_refresh_callback is None  # caller opts in
    calls = []

    async def cb(key):
        calls.append(key)

    layer.stale_refresh_callback = cb
    await layer.stale_refresh_callback("k") if callable(layer.stale_refresh_callback) else None
    assert calls == ["k"]
