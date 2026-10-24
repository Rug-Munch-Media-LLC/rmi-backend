"""Tests for the free/keyless chart-data adapters and the fallback ladder.

HTTP is exercised through respx (real request/parse path, no patching), and the
ladder is exercised through its ``sources`` DI seam.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.routers.rugcharts.helpers.ohlcv import _fetch_ohlcv_gecko_reason
from app.routers.rugcharts.helpers.ohlcv_binance import fetch_ohlcv_binance
from app.routers.rugcharts.helpers.ohlcv_coingecko import fetch_ohlcv_coingecko
from app.routers.rugcharts.helpers.ohlcv_dexscreener import fetch_ohlcv_dexscreener_snapshot
from app.routers.rugcharts.helpers.ohlcv_fallback import _binance, fetch_ohlcv
from app.routers.rugcharts.helpers.ohlcv_schema import is_coarse, normalize_candles

SOL = "So11111111111111111111111111111111111111112"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
POOL = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"


def _klines(count: int = 3) -> list[list[str]]:
    """Minimal Binance kline payload, one hour apart."""
    return [
        [1792821600000 + i * 3600000, "1.0", "1.5", "0.9", "1.2", "100.0", 0, "0", 1, "0", "0", "0"]
        for i in range(count)
    ]


def test_normalize_sorts_dedupes_and_drops_invalid():
    rows = [
        [1792825200, 2, 3, 1, 2.5, 10],
        [1792821600, 1, 2, 0.5, 1.5, 5],
        [1792825200, 2, 3, 1, 2.6, 11],  # duplicate timestamp wins
        [0, 1, 1, 1, 1, 1],  # bad time
        [1792828800, 1, 0.5, 1, 1, 1],  # high < low
        [1792828800, 1, 1],  # short row
        "junk",
    ]
    candles = normalize_candles(rows)
    assert [c["time"] for c in candles] == [1792821600, 1792825200]
    assert candles[1]["close"] == 2.6


def test_normalize_accepts_dicts_and_rejects_non_sequences():
    assert normalize_candles([{"time": 10, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 0}])
    assert normalize_candles("nope") == []
    assert normalize_candles(None) == []


def test_is_coarse_flags_hourly_feed_for_minute_request():
    hourly = [{"time": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]
    hourly.append({**hourly[0], "time": 3600})
    assert is_coarse(hourly, "1m") is True
    assert is_coarse(hourly, "1h") is False


@respx.mock
async def test_binance_falls_through_geo_blocked_host():
    respx.get("https://data-api.binance.vision/api/v3/klines").mock(
        return_value=httpx.Response(451, json={"msg": "restricted location"})
    )
    ok = respx.get("https://api.binance.com/api/v3/klines").mock(return_value=httpx.Response(200, json=_klines()))
    candles, reason = await fetch_ohlcv_binance("solana", SOL, "1h", 3)
    assert reason is None
    assert ok.called
    assert len(candles) == 3
    assert candles[0]["time"] == 1792821600
    assert candles[0]["close"] == 1.2


async def test_binance_unsupported_asset_returns_reason():
    candles, reason = await fetch_ohlcv_binance("solana", "SomeRandomMint111", "1h", 3)
    assert candles == []
    assert reason == "unsupported_asset"


@respx.mock
async def test_coingecko_rate_limit_is_reported_not_raised():
    respx.get("https://api.coingecko.com/api/v3/coins/solana/ohlc").mock(
        return_value=httpx.Response(429, json={"status": {"error_code": 429}})
    )
    candles, reason = await fetch_ohlcv_coingecko("solana", SOL, "1h", 10)
    assert candles == []
    assert reason == "rate_limited"


@respx.mock
async def test_coingecko_rows_have_zero_volume():
    rows = [[1792821600000, 1.0, 1.2, 0.9, 1.1], [1792825200000, 1.1, 1.3, 1.0, 1.25]]
    respx.get("https://api.coingecko.com/api/v3/coins/ethereum/ohlc").mock(return_value=httpx.Response(200, json=rows))
    candles, reason = await fetch_ohlcv_coingecko("ethereum", WETH, "1h", 10)
    assert reason is None
    assert [c["volume"] for c in candles] == [0.0, 0.0]
    assert candles[-1]["close"] == 1.25


@respx.mock
async def test_dexscreener_snapshot_is_one_flat_candle():
    respx.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{POOL}").mock(
        return_value=httpx.Response(200, json={"pairs": [{"priceUsd": "101.5"}]})
    )
    candles, reason = await fetch_ohlcv_dexscreener_snapshot("solana", POOL)
    assert reason is None
    assert len(candles) == 1
    candle = candles[0]
    assert {candle["open"], candle["high"], candle["low"], candle["close"]} == {101.5}
    assert candle["volume"] == 0.0


@respx.mock
async def test_dexscreener_missing_pair_is_empty_payload():
    respx.get(f"https://api.dexscreener.com/latest/dex/pairs/base/{POOL}").mock(
        return_value=httpx.Response(200, json={"pairs": []})
    )
    candles, reason = await fetch_ohlcv_dexscreener_snapshot("base", POOL)
    assert candles == []
    assert reason == "empty_payload"


async def _fake(rows: int, reason: str | None = None):
    async def _source(pair, chain, timeframe, limit):
        return (
            [{"time": 1, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}] * rows,
            reason,
        )

    return _source


async def test_ladder_uses_first_source_with_rows():
    sources = {
        "clickhouse": await _fake(0),
        "geckoterminal": await _fake(2),
        "binance": await _fake(5),
        "coingecko": await _fake(5),
        "dexscreener": await _fake(1),
    }
    candles, meta = await fetch_ohlcv(POOL, "solana", "1h", 2, sources=sources)
    assert len(candles) == 2
    assert meta["source"] == "geckoterminal"
    assert meta["scope"] == "pool"
    assert meta["degraded"] is False
    assert meta["reason"] is None
    assert [a["source"] for a in meta["attempts"]] == ["clickhouse", "geckoterminal"]


async def test_ladder_marks_snapshot_degraded():
    sources = {
        "clickhouse": await _fake(0),
        "geckoterminal": await _fake(0),
        "dexscreener": await _fake(1),
    }
    candles, meta = await fetch_ohlcv(POOL, "solana", "1h", 1, sources=sources)
    assert len(candles) == 1
    assert meta["source"] == "dexscreener"
    assert meta["degraded"] is True


async def test_ladder_all_sources_empty_returns_null_plus_reason():
    sources = {
        "clickhouse": await _fake(0),
        "geckoterminal": await _fake(0, "rate_limited"),
        "binance": await _fake(0, "geo_blocked"),
        "coingecko": await _fake(0, "rate_limited"),
        "dexscreener": await _fake(0, "not_found"),
    }
    candles, meta = await fetch_ohlcv(POOL, "base", "1h", 5, sources=sources)
    assert candles == []
    assert meta["source"] is None
    assert meta["reason"] == "not_found"
    assert meta["rows"] == 0
    assert len(meta["attempts"]) == 5


async def test_ladder_unknown_chain_uses_pool_safe_default():
    sources = {"clickhouse": await _fake(0), "geckoterminal": await _fake(0, "rate_limited")}
    candles, meta = await fetch_ohlcv(POOL, "unknownchain", "1h", 5, sources=sources)
    assert candles == []
    assert [a["source"] for a in meta["attempts"]] == ["clickhouse", "geckoterminal", "dexscreener"]
    assert meta["attempts"][2]["reason"] == "source_unavailable"
    assert meta["reason"] == "source_unavailable"


@pytest.mark.parametrize("timeframe", ["1m", "1h", "1d"])
async def test_ladder_passes_timeframe_through(timeframe):
    seen: list[str] = []

    async def _source(pair, chain, tf, limit):
        seen.append(tf)
        return [], "not_found"

    await fetch_ohlcv(POOL, "solana", timeframe, 5, sources={"clickhouse": _source})
    assert seen
    assert set(seen) == {timeframe}


@respx.mock
async def test_gecko_reports_rate_limit_instead_of_empty():
    respx.get(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{POOL}/ohlcv/hour").mock(
        return_value=httpx.Response(429, json={"status": {"error_code": 429}})
    )
    candles, reason = await _fetch_ohlcv_gecko_reason(POOL, "solana", "1h", 10)
    assert candles == []
    assert reason == "rate_limited"


@respx.mock
async def test_binance_uses_pool_base_token_resolved_from_gecko():
    respx.get(
        "https://api.geckoterminal.com/api/v2/networks/eth/pools/0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "attributes": {"name": "WETH / USDC"},
                    "relationships": {"base_token": {"data": {"id": f"eth_{WETH.lower()}"}}},
                }
            },
        )
    )
    klines = respx.get("https://data-api.binance.vision/api/v3/klines").mock(
        return_value=httpx.Response(200, json=_klines())
    )
    candles, reason = await _binance("0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc", "ethereum", "1h", 3)
    assert reason is None
    assert len(candles) == 3
    assert "ETHUSDT" in klines.calls.last.request.url.params["symbol"]
