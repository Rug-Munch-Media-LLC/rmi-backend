"""Unit tests for rugmunchbot DeFi market-depth handlers (defi.py).

Tests pure logic only (filtering, ranking, rendering, chain resolution).
Network calls and Telegram updates are not exercised here.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.handlers.defi import (
    _health_emoji,
    _rank_protocols,
    _render_lpcheck_text,
    _resolve_chain,
    _yield_candidates,
)


def _pool(apy: float, tvl: float, *, outlier: bool = False, stable: bool = False) -> dict:
    return {
        "symbol": "TEST",
        "project": "test-project",
        "chain": "Ethereum",
        "tvlUsd": tvl,
        "apy": apy,
        "outlier": outlier,
        "stablecoin": stable,
        "ilRisk": "no",
    }


class TestYieldCandidates:
    def test_ranks_by_apy_desc(self) -> None:
        pools = [_pool(8.0, 2_000_000), _pool(50.0, 2_000_000), _pool(20.0, 2_000_000)]
        out = _yield_candidates(pools)
        assert [round(p["apy"], 1) for p in out] == [50.0, 20.0, 8.0]

    def test_drops_outlier_and_low_apy_and_dust(self) -> None:
        pools = [
            _pool(500.0, 2_000_000),  # above 200% APY cap -> dropped
            _pool(3.0, 2_000_000),  # below min APY -> dropped
            _pool(10.0, 5_000),  # below min TVL -> dropped
            _pool(10.0, 2_000_000, outlier=True),  # outlier -> dropped
            _pool(15.0, 2_000_000),  # valid
        ]
        out = _yield_candidates(pools)
        assert len(out) == 1
        assert out[0]["apy"] == 15.0

    def test_stable_only_filter(self) -> None:
        pools = [_pool(10.0, 2_000_000, stable=True), _pool(12.0, 2_000_000, stable=False)]
        out = _yield_candidates(pools, stable_only=True)
        assert len(out) == 1
        assert out[0]["stablecoin"] is True


class TestChainResolution:
    def test_alias_maps(self) -> None:
        assert _resolve_chain("sol", "0xdead") == "solana"
        assert _resolve_chain("eth", "0xdead") == "ethereum"

    def test_evm_detect_defaults_to_ethereum(self) -> None:
        assert _resolve_chain(None, "0x0d4a11d5EEaaC28EC3F61d100daF4d40471f1852") == "ethereum"

    def test_unknown_address_defaults_ethereum(self) -> None:
        assert _resolve_chain(None, "not-an-address") == "ethereum"


class TestHealthEmoji:
    def test_score_bands(self) -> None:
        assert _health_emoji(90) == "\U0001f7e2"
        assert _health_emoji(70) == "\U0001f7e1"
        assert _health_emoji(30) == "\U0001f534"


class TestProtocolRanking:
    def test_excludes_cex(self) -> None:
        protocols = [
            {"name": "Binance", "category": "CEX", "tvl": 139e9},
            {"name": "Lido", "category": "Liquid Staking", "tvl": 18e9},
            {"name": "Aave", "category": "Lending", "tvl": 14e9},
        ]
        out = _rank_protocols(protocols, limit=5)
        names = [p["name"] for p in out]
        assert "Binance" not in names
        assert names == ["Lido", "Aave"]

    def test_limit(self) -> None:
        protocols = [{"name": f"P{i}", "category": "DeFi", "tvl": float(i)} for i in range(1, 15)]
        out = _rank_protocols(protocols, limit=3)
        assert len(out) == 3


class TestLPCheckRender:
    def test_renders_full_health(self) -> None:
        health = {
            "lp_address": "0xabc",
            "chain": "ethereum",
            "token_pair": "ETH/USDC",
            "total_liquidity_usd": 1_234_567,
            "liquidity_change_24h": -12.5,
            "largest_holder_pct": 22.0,
            "holder_count": 88,
            "lp_locked_pct": 91.0,
            "health_score": 85,
            "alerts": [],
        }
        text = _render_lpcheck_text(health)
        assert "ETH/USDC" in text
        assert "85/100" in text
        assert "91.0%" in text
        assert "22.0%" in text

    def test_renders_alerts(self) -> None:
        health = {
            "chain": "solana",
            "token_pair": "SOL/USDC",
            "total_liquidity_usd": 5_000,
            "largest_holder_pct": 60.0,
            "lp_locked_pct": 10.0,
            "health_score": 30,
            "alerts": ["Single holder dominates 60% of LP", "LP only 10% locked"],
        }
        text = _render_lpcheck_text(health)
        assert "Single holder dominates" in text
        assert "LP only 10% locked" in text
