"""Tests for the restored RugMaps engine entity/risk wrapper methods.

Regression coverage for the 2026-08-07 fix ("fix(rugmaps): restore
entity/risk methods"): ``BubbleMapsPro._get_entity_info`` and
``BubbleMapsPro._calculate_risk`` were dead references — the class no longer
exposed them and callers (``rugmaps_router`` /entity, ``rugmaps_ai``) would
hit ``AttributeError``. The thin wrappers delegate to
``graph.get_entity_info`` / ``graph.calculate_risk`` so callers have a stable
method on the engine.

Also covers the relaxed ``_get_transactions`` signature
(``center_address``/``depth`` now optional).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.rugmaps.engine import BubbleMapsPro


@pytest.fixture
def engine() -> BubbleMapsPro:
    bp = BubbleMapsPro()
    bp.transaction_cache = {}
    bp.entity_cache = {}
    return bp


# ═══════════════════════════════════════════════
# _get_entity_info — restored wrapper
# ═══════════════════════════════════════════════


class TestGetEntityInfoWrapper:
    @pytest.mark.asyncio
    async def test_delegates_to_graph_entity_lookup(self, engine: BubbleMapsPro) -> None:
        """The wrapper must return the graph's entity info for an address."""
        entity = {"address": "0xabc", "label": "exchange", "name": "Binance Hot"}
        with patch("app.domains.scanner.rugmaps.engine.get_entity_info", new=AsyncMock(return_value=entity)):
            result = await engine._get_entity_info("0xabc")
        assert result == entity

    @pytest.mark.asyncio
    async def test_returns_none_when_graph_has_no_label(self, engine: BubbleMapsPro) -> None:
        """Unknown wallets yield None (fail-open, not an exception)."""
        with patch("app.domains.scanner.rugmaps.engine.get_entity_info", new=AsyncMock(return_value=None)):
            result = await engine._get_entity_info("0xunknown")
        assert result is None

    @pytest.mark.asyncio
    async def test_forwards_the_address_argument(self, engine: BubbleMapsPro) -> None:
        """The address argument must reach the underlying graph call."""
        with patch("app.domains.scanner.rugmaps.engine.get_entity_info", new=AsyncMock(return_value=None)) as mock_gi:
            await engine._get_entity_info("0xforwarded")
        mock_gi.assert_awaited_once_with("0xforwarded")


# ═══════════════════════════════════════════════
# _calculate_risk — restored wrapper
# ═══════════════════════════════════════════════


class TestCalculateRiskWrapper:
    def test_empty_transactions_returns_unknown(self, engine: BubbleMapsPro) -> None:
        """Empty transaction lists are 'unknown' risk."""
        score, level = engine._calculate_risk([], 0)
        assert score == 0.0
        assert level == "unknown"

    def test_large_transfer_raises_risk(self, engine: BubbleMapsPro) -> None:
        """A large transfer (>1M) counts as a suspicious pattern."""
        txs = [{"from": "a", "to": "b", "value": 5_000_000, "method": "transfer"}]
        score, level = engine._calculate_risk(txs, 10)
        assert score >= 20.0
        assert level in ("low", "medium", "high", "critical")

    def test_known_scammer_flag_scores_high(self, engine: BubbleMapsPro) -> None:
        """'known_scammer' flags produce a high risk score."""
        txs = [
            {
                "from": "a",
                "to": "b",
                "value": 100,
                "method": "transfer",
                "flags": ["known_scammer"],
            }
            for _ in range(4)
        ]
        score, level = engine._calculate_risk(txs, 10)
        assert score >= 60.0
        assert level in ("high", "critical")

    def test_forwards_node_count(self, engine: BubbleMapsPro) -> None:
        """The node_count argument reaches the underlying graph function."""
        txs = [{"from": "a", "to": "b", "value": 100, "method": "transfer"}]
        # With a tiny node_count the concentration check adds a pattern;
        # with a large node_count it does not. Verify delegation by checking
        # the graph result matches the wrapper result for the same inputs.
        from app.domains.scanner.rugmaps.graph import calculate_risk

        expected = calculate_risk(txs, 1)
        actual = engine._calculate_risk(txs, 1)
        assert actual == expected


# ═══════════════════════════════════════════════
# _get_transactions — relaxed signature
# ═══════════════════════════════════════════════


class TestGetTransactionsSignature:
    @pytest.mark.asyncio
    async def test_cached_transactions_served_without_fetch(self, engine: BubbleMapsPro) -> None:
        """The relaxed signature still serves cached transactions."""
        txs = [{"from": "wallet", "to": "counterparty", "value": 100, "timestamp": 1700000000}]
        engine.transaction_cache["0xwallet"] = list(txs)
        result = await engine._get_transactions("0xwallet")
        assert result == txs

    @pytest.mark.asyncio
    async def test_uncached_address_falls_through_to_network(self, engine: BubbleMapsPro) -> None:
        """Missing cache entries attempt a real fetch rather than crashing."""
        with patch.object(engine, "_fetch_evm_transactions", new=AsyncMock(return_value=[])) as mock_fetch:
            result = await engine._get_transactions("0xuncached", "0xcenter")
        assert result == []
        mock_fetch.assert_awaited_once()
