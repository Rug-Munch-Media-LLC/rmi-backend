"""Live trades — normalization, dedupe, fallback and no-fabrication guarantees.

The service takes its sources and cache store as arguments, so every test here
injects fakes instead of patching module attributes. The point of these tests is
that the service can NEVER emit a trade that a source did not report.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.trades import service
from app.trades.service import TradeDeps
from app.trades.gt_trades import dedupe, mark_whales
from app.trades.schema import SUPPORTED_CHAINS, Trade, canon_chain, normalize_geckoterminal_trade
from app.trades.sources import SourceFn, SourceMap, default_sources


def _gt_row(
    *, tx: str = "sig1", kind: str = "sell", ts: str = "2026-10-24T07:53:52Z"
) -> dict[str, Any]:
    return {
        "attributes": {
            "tx_hash": tx,
            "tx_from_address": "walletA",
            "kind": kind,
            "from_token_address": "BONK",
            "to_token_address": "SOL",
            "from_token_amount": "1070477.9",
            "to_token_amount": "0.0318",
            "price_from_in_usd": "0.0000030",
            "price_to_in_usd": "101.9",
            "volume_in_usd": "3.24",
            "block_timestamp": ts,
        }
    }


def _fn(result: list[Trade] | Exception) -> SourceFn:
    """A source that returns ``result``, or raises it when it is an exception."""

    async def fetch(network: str, token: str, limit: int) -> list[Trade]:
        if isinstance(result, Exception):
            raise result
        return result

    return fetch


class FakeStore:
    """In-memory ``TradeStore`` — proves caching without Redis or patching."""

    def __init__(self, hit: dict[str, Any] | None = None) -> None:
        self.hit = hit
        self.written: list[tuple[str, str, dict[str, Any]]] = []

    async def read(self, chain: str, token: str) -> dict[str, Any] | None:
        return self.hit

    async def write(self, chain: str, token: str, payload: dict[str, Any]) -> None:
        self.written.append((chain, token, payload))


class TestChainCanon:
    def test_ethereum_maps_to_eth(self):
        assert canon_chain("ethereum") == "eth"

    def test_unknown_passes_through(self):
        assert canon_chain("weirdchain") == "weirdchain"


class TestNormalize:
    def test_base_token_sell_keeps_kind(self):
        trade = normalize_geckoterminal_trade(
            _gt_row(), chain="solana", token="BONK", pool="p1", base_token="BONK"
        )
        assert trade is not None
        assert trade.side == "sell"
        assert trade.amount_token == pytest.approx(1070477.9)
        assert trade.wallet == "walletA"
        expected = int(datetime(2026, 10, 24, 7, 53, 52, tzinfo=UTC).timestamp() * 1000)
        assert trade.timestamp_ms == expected

    def test_quote_token_inverts_side(self):
        trade = normalize_geckoterminal_trade(
            _gt_row(), chain="solana", token="SOL", pool="p1", base_token="BONK"
        )
        assert trade is not None
        assert trade.side == "buy"

    def test_missing_tx_is_dropped(self):
        row = {"attributes": {"kind": "sell"}}
        assert (
            normalize_geckoterminal_trade(
                row, chain="solana", token="BONK", pool="p", base_token="BONK"
            )
            is None
        )

    def test_unknown_kind_is_dropped(self):
        row = _gt_row(kind="mint")
        assert (
            normalize_geckoterminal_trade(
                row, chain="solana", token="BONK", pool="p", base_token="BONK"
            )
            is None
        )


class TestDedupe:
    def test_same_triple_collapses(self):
        trades = [
            Trade(chain="solana", token="BONK", tx="t1", side="buy", wallet="w"),
            Trade(chain="solana", token="BONK", tx="t1", side="buy", wallet="w"),
            Trade(chain="solana", token="BONK", tx="t1", side="sell", wallet="w2"),
        ]
        assert len(dedupe(trades)) == 2

    def test_whale_flag(self):
        trades = [Trade(chain="solana", token="B", tx="t", side="buy", amount_usd=25_000)]
        assert mark_whales(trades)[0].is_whale is True


class TestSourceRegistry:
    def test_every_supported_chain_has_sources(self):
        sources = default_sources()
        assert set(SUPPORTED_CHAINS) == set(sources)

    def test_solana_tries_four_sources_in_order(self):
        labels = [label for label, _ in default_sources()["solana"]]
        assert labels == ["geckoterminal", "solanatracker", "birdeye", "gmgn"]

    def test_evm_chains_skip_solanatracker(self):
        for chain in ("eth", "base"):
            assert "solanatracker" not in [label for label, _ in default_sources()[chain]]


class TestServiceFallback:
    async def test_uses_first_source_with_rows(self):
        good = [Trade(chain="solana", token="BONK", tx="t1", side="buy", wallet="w", amount_usd=5)]
        sources: SourceMap = {
            "solana": [("geckoterminal", _fn(good)), ("solanatracker", _fn([]))],
        }
        store = FakeStore()
        result = await service.get_token_trades(
            chain="solana", token="BONK", limit=10, deps=TradeDeps(sources, store)
        )
        assert result.source == "geckoterminal"
        assert len(result.trades) == 1
        assert result.tried == ["geckoterminal"]
        assert store.written[0][:2] == ("solana", "BONK")

    async def test_all_sources_empty_yields_no_trades(self):
        sources: SourceMap = {
            "solana": [
                ("geckoterminal", _fn([])),
                ("solanatracker", _fn([])),
                ("birdeye", _fn([])),
                ("gmgn", _fn([])),
            ]
        }
        result = await service.get_token_trades(
            chain="solana", token="BONK", limit=10, deps=TradeDeps(sources, FakeStore())
        )
        assert result.trades == []
        assert result.source == "none"
        assert len(result.tried) == 4

    async def test_raising_source_is_skipped(self):
        good = [Trade(chain="eth", token="WETH", tx="0x1", side="buy", amount_usd=9)]
        sources: SourceMap = {
            "eth": [
                ("geckoterminal", _fn(RuntimeError("provider down"))),
                ("birdeye", _fn(good)),
                ("gmgn", _fn([])),
            ]
        }
        result = await service.get_token_trades(
            chain="eth", token="WETH", limit=10, deps=TradeDeps(sources, FakeStore())
        )
        assert result.source == "birdeye"
        assert result.tried == ["geckoterminal", "birdeye"]

    async def test_cache_hit_skips_sources(self):
        cached = {
            "trades": [
                Trade(
                    chain="solana", token="BONK", tx="t9", side="buy", amount_usd=100
                ).model_dump()
            ],
            "source": "geckoterminal",
            "sources_tried": ["geckoterminal"],
        }
        called: list[str] = []

        async def _record(network: str, token: str, limit: int) -> list[Trade]:
            called.append(network)
            return []

        result = await service.get_token_trades(
            chain="solana",
            token="BONK",
            limit=10,
            deps=TradeDeps({"solana": [("geckoterminal", _record)]}, FakeStore(hit=cached)),
        )
        assert called == []
        assert result.cached is True
        assert result.trades[0].tx == "t9"

    async def test_min_usd_filters(self):
        rows = [
            Trade(chain="solana", token="B", tx="a", side="buy", amount_usd=1),
            Trade(chain="solana", token="B", tx="b", side="buy", amount_usd=5000),
        ]
        result = await service.get_token_trades(
            chain="solana",
            token="B",
            limit=10,
            min_usd=100,
            deps=TradeDeps({"solana": [("geckoterminal", _fn(rows))]}, FakeStore()),
        )
        assert [t.tx for t in result.trades] == ["b"]

    async def test_unsupported_chain_returns_nothing(self):
        result = await service.get_token_trades(
            chain="dogechain",
            token="X",
            limit=10,
            deps=TradeDeps(default_sources(), FakeStore()),
        )
        assert result.trades == []
        assert result.tried == ["unsupported_chain"]
