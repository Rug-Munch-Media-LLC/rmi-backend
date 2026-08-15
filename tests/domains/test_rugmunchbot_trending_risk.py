"""Chain-filtering + risk-ranking tests for the trending tools.

Owned by the "answer relevance" fix (2026-08-15):
  * ``tools/trending._filter_tokens_by_chain`` — the user's requested chain
    actually filters the cross-chain feed (a "solana" question must never
    return $BAG / $Z / $CASHELON from other chains).
  * ``tools/trend_algo._sketch_score`` + ``rank="risk"`` — "riskiest /
    sketchiest picks" ranks by risk (honeypot / rug-pull / risk_score first)
    instead of by % change, and KEEPS flagged tokens that trend mode excludes.

Pure functions are tested directly; the rank-mode behavior of
``trend_by_chain`` is tested with the candidate/metric fetchers monkeypatched
(no live network).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.telegram.rugmunchbot.tools import trend_algo
from app.domains.telegram.rugmunchbot.tools.intent import build_tool_prompt_block
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult
from app.domains.telegram.rugmunchbot.tools.trending import _filter_tokens_by_chain


def _tok(symbol: str, chain: str) -> dict[str, Any]:
    return {"symbol": symbol, "name": symbol, "address": f"addr_{symbol}", "chain": chain, "source": "test"}


# ══════════════════════════════════════════════════════════════════════════
# build_tool_prompt_block — risk fields survive prompt rendering
# ══════════════════════════════════════════════════════════════════════════


class TestBuildToolPromptBlock:
    def test_risk_flags_and_sketch_score_survive_list_rendering(self) -> None:
        result = ToolResult(
            tool="trend_by_chain",
            ok=True,
            data={
                "chain": "solana",
                "rank_mode": "risk",
                "tokens": [
                    {
                        "symbol": "SCAM",
                        "chain": "solana",
                        "address": "addr_SCAM",
                        "sketch_score": 200.0,
                        "risk_flags": ["honeypot", "risk_score=95/100"],
                        "score": 12.0,
                    },
                    {
                        "symbol": "PUMP",
                        "chain": "solana",
                        "address": "addr_PUMP",
                        "sketch_score": 29.1,
                        "risk_flags": ["unscanned"],
                        "score": 88.0,
                    },
                ],
            },
            symbols=["SCAM", "PUMP"],
            addresses=["addr_SCAM", "addr_PUMP"],
            source="own:scanner/recent, own:market/trending",
        )
        block = build_tool_prompt_block({"trend_by_chain": result})
        # The LLM must SEE the flags so it can explain WHY each pick is sketchy.
        assert "risk_flags=['honeypot', 'risk_score=95/100']" in block
        assert "sketch_score=200.0" in block
        assert "symbol=SCAM" in block
        assert "symbol=PUMP" in block


# ══════════════════════════════════════════════════════════════════════════
# trending._filter_tokens_by_chain — chain scoping on the raw feed
# ══════════════════════════════════════════════════════════════════════════


class TestFilterTokensByChain:
    def test_all_chain_passthrough(self) -> None:
        tokens = [_tok("BAG", "robinhood"), _tok("Z", "bsc"), _tok("BONK", "solana")]
        assert _filter_tokens_by_chain(tokens, "all") == tokens
        assert _filter_tokens_by_chain(tokens, "") == tokens
        assert _filter_tokens_by_chain(tokens, "multi") == tokens

    def test_solana_keeps_only_solana(self) -> None:
        tokens = [_tok("BAG", "robinhood"), _tok("Z", "bsc"), _tok("BONK", "solana")]
        filtered = _filter_tokens_by_chain(tokens, "solana")
        assert [t["symbol"] for t in filtered] == ["BONK"]

    def test_alias_normalization(self) -> None:
        # "sol" / "eth" aliases resolve to canonical chain ids.
        tokens = [_tok("BONK", "solana"), _tok("WIF", "sol")]
        assert len(_filter_tokens_by_chain(tokens, "sol")) == 2
        tokens = [_tok("UNI", "ethereum"), _tok("PEPE", "eth")]
        assert len(_filter_tokens_by_chain(tokens, "eth")) == 2

    def test_multi_coin_gecko_entries_dropped_for_scoped_question(self) -> None:
        # CoinGecko "multi" entries carry no on-chain address — they must not
        # leak into a chain-scoped answer.
        tokens = [_tok("BTC", "multi"), _tok("BONK", "solana")]
        filtered = _filter_tokens_by_chain(tokens, "solana")
        assert [t["symbol"] for t in filtered] == ["BONK"]

    def test_case_insensitive(self) -> None:
        tokens = [_tok("BONK", "Solana"), _tok("WIF", "SOLANA")]
        assert len(_filter_tokens_by_chain(tokens, "solana")) == 2


# ══════════════════════════════════════════════════════════════════════════
# trend_algo._sketch_score — risk-first ranking components
# ══════════════════════════════════════════════════════════════════════════


class TestSketchScore:
    def _metrics(self, **over: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "volume_24h": 1_000_000,
            "liquidity_usd": 100_000,
            "price_change_24h": 20.0,
            "buys_24h": 100,
            "sells_24h": 50,
            "age_hours": 48.0,
        }
        base.update(over)
        return base

    def test_flagged_honeypot_scores_above_plain_unscanned(self) -> None:
        flagged = trend_algo._sketch_score(self._metrics(), {"is_honeypot": True, "risk_score": 90})
        plain = trend_algo._sketch_score(self._metrics(), None)
        assert flagged["sketch_score"] > plain["sketch_score"]
        assert "honeypot" in flagged["flags"]

    def test_higher_risk_score_ranks_higher(self) -> None:
        high = trend_algo._sketch_score(self._metrics(), {"risk_score": 90, "is_honeypot": False, "is_rug_pull": False})
        low = trend_algo._sketch_score(self._metrics(), {"risk_score": 40, "is_honeypot": False, "is_rug_pull": False})
        assert high["sketch_score"] > low["sketch_score"]
        assert "risk_score=90/100" in high["flags"]

    def test_rug_pull_flag_present(self) -> None:
        s = trend_algo._sketch_score(self._metrics(), {"is_rug_pull": True})
        assert "rug_pull" in s["flags"]

    def test_thin_liquidity_fresh_launch_violent_move_flags(self) -> None:
        s = trend_algo._sketch_score(
            self._metrics(liquidity_usd=1_000, age_hours=2.0, price_change_24h=150.0),
            None,
        )
        assert "thin_liquidity" in s["flags"]
        assert "fresh_launch" in s["flags"]
        assert "violent_moves" in s["flags"]
        assert "unscanned" in s["flags"]

    def test_low_risk_established_token_scores_low(self) -> None:
        s = trend_algo._sketch_score(
            self._metrics(liquidity_usd=5_000_000, age_hours=5000.0, price_change_24h=1.0),
            {"risk_score": 15, "is_honeypot": False, "is_rug_pull": False},
        )
        assert s["sketch_score"] < 40.0


# ══════════════════════════════════════════════════════════════════════════
# trend_by_chain(rank=...) — end-to-end rank behavior with fakes
# ══════════════════════════════════════════════════════════════════════════


class TestTrendByChainRiskRank:
    @pytest.fixture(autouse=True)
    def _no_network(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fake every network-touching fetch; clear Birdeye key."""
        monkeypatch.delenv("BIRDEYE_API_KEY", raising=False)

        async def fake_scanner() -> list[dict[str, Any]]:
            return [
                {
                    "address": "addr_SCAM",
                    "chain": "solana",
                    "symbol": "SCAM",
                    "risk_score": 95,
                    "is_honeypot": True,
                    "is_rug_pull": False,
                    "source": "own:scanner/recent",
                }
            ]

        async def fake_trending() -> list[dict[str, Any]]:
            return [{"address": "addr_PUMP", "chain": "solana", "symbol": "PUMP", "source": "own:market/trending"}]

        async def fake_launches() -> list[dict[str, Any]]:
            return []

        async def fake_profiles() -> list[dict[str, Any]]:
            return []

        async def fake_metrics(address: str) -> dict[str, Any]:
            sym = "SCAM" if address == "addr_SCAM" else "PUMP"
            return {
                "address": address,
                "chain": "solana",
                "symbol": sym,
                "name": sym,
                "dex_id": "raydium",
                "price_usd": 0.001,
                "volume_24h": 200_000,
                "liquidity_usd": 8_000,
                "price_change_24h": 120.0,
                "buys_24h": 80,
                "sells_24h": 20,
                "age_hours": 3.0,
                "url": "",
            }

        monkeypatch.setattr(trend_algo, "_scanner_candidates", fake_scanner)
        monkeypatch.setattr(trend_algo, "_trending_candidates", fake_trending)
        monkeypatch.setattr(trend_algo, "_launch_candidates", fake_launches)
        monkeypatch.setattr(trend_algo, "_dex_profile_candidates", fake_profiles)
        monkeypatch.setattr(trend_algo, "_fetch_pair_metrics", fake_metrics)
        monkeypatch.setattr(trend_algo, "_birdeye_holders", lambda address, chain: None)

    async def test_trend_mode_excludes_flagged_honeypot(self) -> None:
        result = await trend_algo.trend_by_chain(chain="solana", rank="trend")
        assert result.ok
        assert result.data["rank_mode"] == "trend"
        syms = [t["symbol"] for t in result.data["tokens"]]
        assert "SCAM" not in syms  # flagged honeypot excluded in trend mode
        assert "PUMP" in syms

    async def test_risk_mode_keeps_flagged_and_ranks_it_first(self) -> None:
        result = await trend_algo.trend_by_chain(chain="solana", rank="risk")
        assert result.ok
        assert result.data["rank_mode"] == "risk"
        tokens = result.data["tokens"]
        syms = [t["symbol"] for t in tokens]
        assert "SCAM" in syms  # flagged honeypot KEPT in risk mode
        assert "PUMP" in syms
        # The flagged honeypot is the sketchiest pick — ranked first.
        assert tokens[0]["symbol"] == "SCAM"
        assert "honeypot" in tokens[0]["risk_flags"]
        assert tokens[0]["sketch_score"] > tokens[1]["sketch_score"]
        # Every token carries an explainable "why".
        for t in tokens:
            assert t["risk_flags"]

    async def test_risk_mode_cross_chain_all(self) -> None:
        result = await trend_algo.trend_by_chain(chain="all", rank="risk")
        assert result.ok
        assert result.data["chain"] == "all"
