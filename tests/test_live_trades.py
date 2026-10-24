"""Live trades — normalization, dedupe, fallback and no-fabrication guarantees.

Pure unit tests: every network source is mocked. The point of these tests is
that the service can NEVER emit a trade that a source did not report.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.trades import service
from app.trades.gt_trades import dedupe, mark_whales
from app.trades.schema import Trade, canon_chain, normalize_geckoterminal_trade


def _gt_row(*, tx: str = "sig1", kind: str = "sell", ts: str = "2026-10-24T07:53:52Z") -> dict[str, Any]:
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


class TestChainCanon:
    def test_ethereum_maps_to_eth(self):
        assert canon_chain("ethereum") == "eth"

    def test_unknown_passes_through(self):
        assert canon_chain("weirdchain") == "weirdchain"


class TestNormalize:
    def test_base_token_sell_keeps_kind(self):
        trade = normalize_geckoterminal_trade(_gt_row(), chain="solana", token="BONK", pool="p1", base_token="BONK")
        assert trade is not None
        assert trade.side == "sell"
        assert trade.amount_token == pytest.approx(1070477.9)
        assert trade.wallet == "walletA"
        expected = int(datetime(2026, 10, 24, 7, 53, 52, tzinfo=UTC).timestamp() * 1000)
        assert trade.timestamp_ms == expected

    def test_quote_token_inverts_side(self):
        trade = normalize_geckoterminal_trade(_gt_row(), chain="solana", token="SOL", pool="p1", base_token="BONK")
        assert trade is not None
        assert trade.side == "buy"

    def test_missing_tx_is_dropped(self):
        row = {"attributes": {"kind": "sell"}}
        assert normalize_geckoterminal_trade(row, chain="solana", token="BONK", pool="p", base_token="BONK") is None

    def test_unknown_kind_is_dropped(self):
        row = _gt_row(kind="mint")
        assert normalize_geckoterminal_trade(row, chain="solana", token="BONK", pool="p", base_token="BONK") is None


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


class TestServiceFallback:
    async def test_uses_first_source_with_rows(self):
        good = [Trade(chain="solana", token="BONK", tx="t1", side="buy", wallet="w", amount_usd=5)]
        with (
            patch.object(service, "_gt", AsyncMock(return_value=good)),
            patch.object(service, "_st", AsyncMock(return_value=[])),
            patch.object(service.cache, "read", AsyncMock(return_value=None)),
            patch.object(service.cache, "write", AsyncMock()),
        ):
            result = await service.get_token_trades(chain="solana", token="BONK", limit=10)
        assert result.source == "geckoterminal"
        assert len(result.trades) == 1
        assert result.tried == ["geckoterminal"]

    async def test_all_sources_empty_yields_no_trades(self):
        with (
            patch.object(service, "_gt", AsyncMock(return_value=[])),
            patch.object(service, "_st", AsyncMock(return_value=[])),
            patch.object(service, "_be", AsyncMock(return_value=[])),
            patch.object(service, "_gm", AsyncMock(return_value=[])),
            patch.object(service.cache, "read", AsyncMock(return_value=None)),
            patch.object(service.cache, "write", AsyncMock()),
        ):
            result = await service.get_token_trades(chain="solana", token="BONK", limit=10)
        assert result.trades == []
        assert result.source == "none"
        assert len(result.tried) == 4

    async def test_raising_source_is_skipped(self):
        good = [Trade(chain="eth", token="WETH", tx="0x1", side="buy", amount_usd=9)]
        boom = AsyncMock(side_effect=RuntimeError("provider down"))
        with (
            patch.object(service, "_gt", boom),
            patch.object(service, "_be", AsyncMock(return_value=good)),
            patch.object(service, "_gm", AsyncMock(return_value=[])),
            patch.object(service.cache, "read", AsyncMock(return_value=None)),
            patch.object(service.cache, "write", AsyncMock()),
        ):
            result = await service.get_token_trades(chain="eth", token="WETH", limit=10)
        assert result.source == "birdeye"
        assert result.tried == ["geckoterminal", "birdeye"]

    async def test_cache_hit_skips_sources(self):
        cached = {
            "trades": [Trade(chain="solana", token="BONK", tx="t9", side="buy", amount_usd=100).model_dump()],
            "source": "geckoterminal",
            "sources_tried": ["geckoterminal"],
        }
        gt = AsyncMock()
        with (
            patch.object(service.cache, "read", AsyncMock(return_value=cached)),
            patch.object(service, "_gt", gt),
        ):
            result = await service.get_token_trades(chain="solana", token="BONK", limit=10)
        gt.assert_not_called()
        assert result.cached is True
        assert result.trades[0].tx == "t9"

    async def test_min_usd_filters(self):
        rows = [
            Trade(chain="solana", token="B", tx="a", side="buy", amount_usd=1),
            Trade(chain="solana", token="B", tx="b", side="buy", amount_usd=5000),
        ]
        with (
            patch.object(service, "_gt", AsyncMock(return_value=rows)),
            patch.object(service.cache, "read", AsyncMock(return_value=None)),
            patch.object(service.cache, "write", AsyncMock()),
        ):
            result = await service.get_token_trades(chain="solana", token="B", limit=10, min_usd=100)
        assert [t.tx for t in result.trades] == ["b"]

    async def test_unsupported_chain_returns_nothing(self):
        with patch.object(service.cache, "read", AsyncMock(return_value=None)):
            result = await service.get_token_trades(chain="dogechain", token="X", limit=10)
        assert result.trades == []
        assert result.tried == ["unsupported_chain"]
