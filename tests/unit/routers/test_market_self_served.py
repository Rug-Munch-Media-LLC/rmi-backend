"""Unit tests for self-served market endpoints (app/routers/v1/market.py).

Covers:
  - Holders: ClickHouse data wins (EVM + Solana paths), DexScreener
    fallback only when ClickHouse is empty, fail-open on CH errors.
  - Trending: own launches + activity scoring, external fallback, empty
    result when no source has data.
  - New pairs: deployer_events path + DexScreener fallback.
  - Whale feed: stablecoin-side USD derivation, news-item shape with
    image_url field, fail-open to empty list.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.routers.v1.market import (
    _stablecoin_value,
    router as market_router,
)


@pytest.fixture
def client():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(market_router, prefix="/api/v1")
    return TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────


class _FakeCH:
    """Minimal clickhouse client stub returning canned result sets."""

    def __init__(self, results: list[list[tuple]]):
        self._results = list(results)
        self.queries: list[str] = []

    def execute(self, sql, params=None):
        self.queries.append(sql)
        if self._results:
            return self._results.pop(0)
        return []


def _patch_ch(fake):
    """Patch the lazy CH client getter used by the market router."""
    import app.routers.v1.market as market

    return patch.object(market, "_ch", return_value=fake)


# ── Holders ───────────────────────────────────────────────────────────


def test_holders_evm_from_clickhouse(client):
    fake = _FakeCH(
        [
            [("0xaaa", 500.0, 12), ("0xbbb", 300.0, 5)],  # holder_balances rows
            [(2,)],  # uniq count
        ]
    )
    with _patch_ch(fake):
        r = client.get("/api/v1/token/ethereum/0xtoken/holders")
    assert r.status_code == 200
    body = r.json()
    # Fast path: pre-aggregated holder_balances table (indexed, no full scan)
    assert body["source"] == "own_clickhouse:holder_balances"
    assert body["total_holders"] == 2
    assert body["holders"][0]["address"] == "0xaaa"
    assert body["holders"][0]["balance"] == 500.0


def test_holders_solana_from_clickhouse(client):
    fake = _FakeCH(
        [
            [("whale1", 1000.0, 3)],
            [(1,)],
        ]
    )
    with _patch_ch(fake):
        r = client.get("/api/v1/token/solana/SomeMint/holders")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "own_clickhouse:solana_spl_balance_changes"
    assert body["holders"][0]["address"] == "whale1"


def test_holders_falls_back_to_dexscreener_when_ch_empty(client):
    fake = _FakeCH([[], []])  # empty balance rows → CH has no data
    fallback = [{"address": "0xccc", "balance": 1.0}]

    async def fake_fallback(chain, address, limit):
        return {
            "chain": chain,
            "token_address": address,
            "total_holders": 1,
            "holders": fallback,
            "source": "dexscreener",
        }

    import app.routers.v1.market as market

    with _patch_ch(fake), patch.object(market, "_holders_fallback", side_effect=fake_fallback):
        r = client.get("/api/v1/token/base/0xtoken/holders")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "dexscreener"
    assert body["holders"] == fallback


def test_holders_fail_open_on_ch_error(client):
    class _Boom:
        def execute(self, sql, params=None):
            raise RuntimeError("clickhouse down")

    with _patch_ch(_Boom()):
        r = client.get("/api/v1/token/ethereum/0xtoken/holders")
    assert r.status_code == 200  # fail-open, never 500
    assert r.json()["source"] in {"dexscreener", "none"}


def test_holders_unknown_chain_passes_through(client):
    # Unknown chains are normalized pass-through (fail-open, no 400)
    fake = _FakeCH([[], []])
    with _patch_ch(fake):
        r = client.get("/api/v1/token/notachain/0xtoken/holders")
    assert r.status_code == 200
    assert r.json()["source"] in {"dexscreener", "none"}


# ── Trending ──────────────────────────────────────────────────────────


def test_trending_from_own_launches(client):
    now = datetime.now(UTC)
    fake = _FakeCH(
        [
            [
                ("solana", now, "Mint1", "creator1", "pumpfun", "sig1", "launchpad", 0.9, 0.0, "high", "BONK"),
                ("base", now, "0xaaa", "", "uniswap", "0xtx", "pair_created", 0.0, 0.0, "high", ""),
            ],
            [("Mint1", 40)],  # solana activity — note: address case preserved
            [],
        ]
    )
    with _patch_ch(fake):
        r = client.get("/api/v1/market/trending")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "own_clickhouse"
    assert body["count"] == 2
    top = body["data"][0]
    assert {"chain", "token_address", "score", "activity_24h", "block_timestamp"} <= set(top)
    # Higher launch signal + activity ranks first
    assert top["launch_signal"] == 0.9


def test_trending_fallback_when_ch_empty(client):
    fake = _FakeCH([[]])

    async def fake_fallback(chain, limit):
        return [{"symbol": "SOL", "source": "dexscreener"}]

    import app.routers.v1.market as market

    with _patch_ch(fake), patch.object(market, "_trending_fallback", side_effect=fake_fallback):
        r = client.get("/api/v1/market/trending")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "dexscreener"
    assert body["data"][0]["symbol"] == "SOL"


def test_trending_empty_when_no_source(client):
    fake = _FakeCH([[]])

    async def fake_fallback(chain, limit):
        return None

    import app.routers.v1.market as market

    with _patch_ch(fake), patch.object(market, "_trending_fallback", side_effect=fake_fallback):
        r = client.get("/api/v1/market/trending")
    assert r.status_code == 200
    assert r.json() == {"data": [], "source": "none", "count": 0}


# ── New pairs ─────────────────────────────────────────────────────────


def test_new_pairs_from_own_clickhouse(client):
    now = datetime.now(UTC)
    fake = _FakeCH([[("base", now, "0xpair", "0xdep", "0xfactory", "0xtx", "pool")]])
    with _patch_ch(fake):
        r = client.get("/api/v1/market/new-pairs")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "own_clickhouse"
    assert body["data"][0]["type"] == "pool"
    assert body["data"][0]["token_address"] == "0xpair"


def test_new_pairs_fallback(client):
    fake = _FakeCH([[]])

    async def fake_fallback(chain, limit):
        return [{"chainId": "solana", "pairAddress": "P1"}]

    import app.routers.v1.market as market

    with _patch_ch(fake), patch.object(market, "_new_pairs_fallback", side_effect=fake_fallback):
        r = client.get("/api/v1/market/new-pairs")
    assert r.status_code == 200
    assert r.json()["source"] == "dexscreener"


# ── Whale feed ────────────────────────────────────────────────────────

_USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


def test_stablecoin_value_derivation():
    # Raw on-chain units: 250,000 USDC = 250_000_000_000 raw (6 decimals).
    usd, asset = _stablecoin_value(_USDC, "0xtok", 250_000_000_000.0, 100.0, 0.0, 0.0, 6, None)
    assert usd == 250_000.0
    assert asset == _USDC
    # stablecoin on token1 side (raw 50,000 USDT = 50_000_000_000)
    usd2, asset2 = _stablecoin_value("0xtok", _USDC, 100.0, 50_000_000_000.0, 0.0, 0.0, None, 6)
    assert usd2 == 50_000.0
    assert asset2 == _USDC
    # OUT leg (sell side): stable exited the pair — still counted
    usd3, _ = _stablecoin_value(_USDC, "0xtok", 0.0, 0.0, 300_000_000_000.0, 0.0, 6, None)
    assert usd3 == 300_000.0
    # unknown stable address → 0
    usd4, asset4 = _stablecoin_value("0xa", "0xb", 1e12, 0, 0, 0, 6, 6)
    assert usd4 == 0.0
    assert asset4 == ""


def test_whale_feed_news_item_shape(client):
    now = datetime.now(UTC)
    fake = _FakeCH(
        [
            [
                (
                    "ethereum",
                    now,
                    "0xhash1",
                    "uniswap",
                    "v3",
                    "0xpair",
                    _USDC,
                    "0xtok",
                    "0xsender",
                    250_000_000_000.0,  # raw USDC in (6 decimals) = $250k
                    100.0,
                    0.0,
                    0.0,
                    "USDC",
                    "PEPE",
                    6,
                    None,
                    250_000.0,  # volume_usd (indexer-precomputed notional)
                )
            ],
            [],  # labels query
            [],  # history query
        ]
    )
    with _patch_ch(fake):
        r = client.get("/api/v1/whales/feed?min_usd=100000")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "own_clickhouse"
    item = body["data"][0]
    # News-item contract: id, title, summary, category, image_url support
    for key in ("id", "title", "summary", "category", "image_url", "usd_value", "tx_hash", "chain"):
        assert key in item, f"missing {key}"
    assert item["category"] == "whale"
    assert item["usd_value"] == 250_000.0
    assert "250,000" in item["title"]
    # Enrichment contract: labels/context/smart-money fields always present
    assert item["wallet_label"] == "unknown"
    assert item["wallet_labels"] == []
    assert item["is_smart_money"] is False
    assert item["context"]["unusual"] is False


def test_whale_feed_fail_open_empty(client):
    class _Boom:
        def execute(self, sql, params=None):
            raise RuntimeError("clickhouse down")

    with _patch_ch(_Boom()):
        r = client.get("/api/v1/whales/feed")
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["source"] == "own_clickhouse"
    assert body["count"] == 0
    assert body["alpha_signal_count"] == 0


def test_whale_feed_unknown_chain_no_stables(client):
    # Chain with no stablecoin map → no USD-derivable swaps → empty, not error
    with _patch_ch(_FakeCH([])):
        r = client.get("/api/v1/whales/feed?chain=fantom")
    assert r.status_code == 200
    assert r.json()["count"] == 0
