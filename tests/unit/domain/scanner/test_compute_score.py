"""Tests for the unified scanner's explainable risk scoring.

Covers ``app.domains.scanner.unified.scoring.compute_score``:

  1. A neutral token with no findings scores at the 50 baseline.
  2. A critical token (honeypot + rug deployer) scores well below 20.
  3. Premium penalties only apply when premium-tier data is present.
  4. The breakdown exposes all five dimensions with correct weights.
  5. The evidence payload carries the raw findings behind the flags.
  6. Green flags push the score above the baseline.
"""

from __future__ import annotations

from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.scoring import (
    DIMENSION_WEIGHTS,
    apply_news_scoring,
    compute_score,
)


def _result(**overrides: object) -> ScanResult:
    """Build a minimal ScanResult for scoring tests."""
    defaults: dict[str, object] = {
        "token_address": "0xTEST123",
        "chain": "solana",
    }
    defaults.update(overrides)
    return ScanResult(**defaults)  # type: ignore[arg-type]


class TestNeutralToken:
    def test_no_findings_scores_at_baseline(self) -> None:
        """A token with no enrichment data scores exactly the 50 baseline."""
        out = compute_score(_result())
        assert out["score"] == 50.0
        assert out["risk_level"] == "medium"  # 40 <= 50 < 60

    def test_clean_token_scores_above_baseline(self) -> None:
        """Green flags (locked LP, distributed holders, clean dev, authentic volume) add points."""
        result = _result(
            liquidity={"locked_pct": 95},
            holders={"top_10_pct": 20},
            dev_reputation={"risk_score": 10},
            volume_auth={"fake_volume_pct": 5},
            modules_run=["liquidity", "holders", "deployer", "volume"],
        )
        out = compute_score(result)
        # +5 each for LP_LOCKED, HOLDER_DISTRIBUTED, DEV_REPUTABLE, VOLUME_AUTHENTIC
        assert out["score"] == 70.0
        assert "LP_LOCKED" in result.green_flags
        assert "HOLDER_DISTRIBUTED" in result.green_flags
        assert "DEV_REPUTABLE" in result.green_flags
        assert "VOLUME_AUTHENTIC" in result.green_flags


class TestCriticalToken:
    def test_honeypot_and_rug_deployer_score_below_20(self) -> None:
        """Honeypot + repeat-rug deployer drives the safety score well below 20."""
        out = compute_score(
            _result(
                modules_run=["token_security", "liquidity", "deployer"],
                security={
                    "checks": {
                        "HP01": {"status": "fail", "details": "GoPlus honeypot=YES"},
                        "CR01": {"status": "fail", "details": "Verified=NO"},
                    }
                },
                rug_patterns={
                    "match_count": 2,
                    "patterns": ["RP01 Rug Pull - Known Pattern", "RP02 Liquidity Removal"],
                },
                dev_reputation={"risk_score": 88, "risk_label": "CRITICAL"},
                liquidity={"locked_pct": 0},
            )
        )
        assert out["score"] < 20
        assert out["risk_level"] == "critical"
        # Each failed security check contributes -5, rug matches -10 each
        assert out["breakdown"]["security"]["delta"] == -30.0
        assert out["breakdown"]["deployer"]["delta"] == -25.0
        assert out["breakdown"]["liquidity"]["delta"] == -20.0
        assert "HP01" in out["breakdown"]["security"]["flags"]
        assert "RUG_PATTERN_MATCH:2" in out["breakdown"]["security"]["flags"]
        assert "DEV_HIGH_RISK" in out["breakdown"]["deployer"]["flags"]


class TestTieredPenaltyScope:
    def test_premium_penalties_only_when_premium_data_present(self) -> None:
        """Premium detections (elite tier) penalize market; free tier gets no such flags."""
        with_premium = compute_score(
            _result(
                modules_run=["premium"],
                premium={
                    "bundle_detect": {"risk": "HIGH"},
                    "wash_trade_detect": {"risk": "CRITICAL"},
                    "sniper_detect": {"risk": "LOW"},  # LOW must not penalize
                },
            )
        )
        without_premium = compute_score(_result(modules_run=[]))

        # Elite scope: two HIGH/CRITICAL premium features -> -20 in market
        assert with_premium["breakdown"]["market"]["delta"] == -20.0
        assert "PREMIUM_BUNDLE_DETECT" in with_premium["breakdown"]["market"]["flags"]
        assert "PREMIUM_WASH_TRADE_DETECT" in with_premium["breakdown"]["market"]["flags"]
        # LOW risk feature must not add a flag
        assert "PREMIUM_SNIPER_DETECT" not in with_premium["breakdown"]["market"]["flags"]

        # Free scope: no premium data -> no premium penalties at all
        assert without_premium["breakdown"]["market"]["delta"] == 0.0
        assert without_premium["breakdown"]["market"]["flags"] == []

    def test_chain_indexer_penalty_scoped_to_market(self) -> None:
        """Chain-indexer findings (on-chain analysis) penalize the market dimension."""
        out = compute_score(
            _result(
                modules_run=["chain_indexer"],
                chain_indexer_patterns={
                    "findings": [
                        {"pattern": "bundler", "detected": True, "risk_score": 30},
                        {"pattern": "mixer", "detected": True, "risk_score": 20},
                    ]
                },
            )
        )
        assert out["breakdown"]["market"]["delta"] == -40.0  # -min(30 + 20, 40)
        assert "ONCHAIN_BUNDLER" in out["breakdown"]["market"]["flags"]
        assert "ONCHAIN_MIXER" in out["breakdown"]["market"]["flags"]


class TestBreakdownShape:
    def test_breakdown_has_all_five_dimensions(self) -> None:
        """Breakdown exposes security/liquidity/holders/deployer/market with weights 35/25/15/15/10."""
        out = compute_score(_result())
        breakdown = out["breakdown"]
        assert set(breakdown.keys()) == {"security", "liquidity", "holders", "deployer", "market"}
        for dim, weight in DIMENSION_WEIGHTS.items():
            entry = breakdown[dim]
            assert entry["max"] == weight
            assert {"score", "max", "delta", "flags"} == set(entry.keys())
            # surviving points are clamped to [0, max]
            assert 0.0 <= entry["score"] <= float(weight)

    def test_surviving_points_reflect_delta(self) -> None:
        """score == clamp(0, max, max + delta) per dimension."""
        out = compute_score(
            _result(
                security={"checks": {"HP01": {"status": "fail"}}},
                liquidity={"locked_pct": 0},
            )
        )
        sec = out["breakdown"]["security"]
        liq = out["breakdown"]["liquidity"]
        assert sec["score"] == 35.0 - 5.0  # max 35 - 5 penalty
        assert sec["delta"] == -5.0
        assert liq["score"] == 25.0 - 20.0  # max 25 - 20 penalty
        assert liq["delta"] == -20.0


class TestEvidence:
    def test_evidence_populated_from_raw_findings(self) -> None:
        """Evidence mirrors the breakdown keys and carries the raw check details."""
        out = compute_score(
            _result(
                modules_run=["token_security", "liquidity", "holders", "deployer", "volume"],
                security={
                    "checks": {
                        "HP01": {"status": "fail", "details": "GoPlus honeypot=YES"},
                        "CR01": {"status": "pass", "details": "Verified=YES"},
                    },
                    "score": 61.2,
                    "band": "HIGH RISK",
                },
                rug_patterns={"match_count": 1, "patterns": ["RP01 Rug Pull - Known Pattern"]},
                liquidity={"locked_pct": 0, "liquidity_usd": 12000},
                holders={"top_10_pct": 92.4, "holder_count": 421},
                holder_health={"gini": 0.91, "hhi": 4100},
                dev_reputation={"deployer_address": "0xdev", "risk_score": 88, "risk_label": "CRITICAL"},
                volume_auth={"fake_volume_pct": 81},
            )
        )
        ev = out["evidence"]
        assert "security" in ev
        assert ev["security"]["checks"]["HP01"]["status"] == "fail"
        assert ev["security"]["checks"]["HP01"]["details"] == "GoPlus honeypot=YES"
        assert ev["security"]["rug_patterns"]["match_count"] == 1
        assert "liquidity" in ev and ev["liquidity"]["locked_pct"] == 0
        assert "holders" in ev and ev["holders"]["top_10_pct"] == 92.4
        assert "holder_health" in ev and ev["holder_health"]["gini"] == 0.91
        assert "deployer" in ev and ev["deployer"]["risk_score"] == 88
        assert "market" in ev and ev["market"]["fake_volume_pct"] == 81

    def test_evidence_empty_for_neutral_token(self) -> None:
        """No data -> no evidence, but the breakdown still has all dimensions."""
        out = compute_score(_result())
        assert out["evidence"] == {}


class TestApplyNewsScoring:
    """News-driven post-scoring bonus/penalty (apply_news_scoring)."""

    def _news_result(
        self,
        *,
        sentiment: str = "BEARISH",
        trending: int = 90,
        total_sources: int = 0,
        on_chain: bool = False,
        score: float = 50.0,
    ) -> ScanResult:
        result = _result(modules_run=["news_sentiment", "news_card"])
        compute_score(result)
        result.safety_score = score
        result.news_sentiment = {
            "result": "scanned",
            "sentiment": sentiment,
            "trending_score": trending,
            "article_count": 3,
        }
        if total_sources or on_chain:
            result.news_card = {
                "result": "scanned",
                "articles": [{"title": "a"}, {"title": "b"}, {"title": "c"}],
                "corroboration": {
                    "total_sources": total_sources,
                    "independent_count": 2,
                    "on_chain_evidence": on_chain,
                },
            }
        return result

    def test_bearish_high_attention_adds_risk(self) -> None:
        """BEARISH + trending > 70 → -5 safety, red flag, market delta -5."""
        result = self._news_result(sentiment="BEARISH", trending=90, score=30.0)
        apply_news_scoring(result)

        assert result.safety_score == 25.0
        assert result.risk_level == "high"  # 20 <= 25 < 40
        assert "NEWS_BEARISH_HIGH_ATTENTION" in result.risk_flags
        assert result.breakdown["market"]["delta"] == -5.0
        assert "NEWS_BEARISH_HIGH_ATTENTION" in result.breakdown["market"]["flags"]

    def test_bullish_high_attention_reduces_risk(self) -> None:
        """BULLISH + trending > 70 → +5 safety, green flag, market delta +5."""
        result = self._news_result(sentiment="BULLISH", trending=90, score=60.0)
        apply_news_scoring(result)

        assert result.safety_score == 65.0
        assert result.risk_level == "low"  # 60 <= 65 < 80
        assert "NEWS_BULLISH_HIGH_ATTENTION" in result.green_flags
        assert result.breakdown["market"]["delta"] == 5.0
        assert "NEWS_BULLISH_HIGH_ATTENTION" in result.breakdown["market"]["flags"]

    def test_low_attention_is_noop(self) -> None:
        """Trending <= 70 → no bonus/penalty applied."""
        result = self._news_result(sentiment="BEARISH", trending=40, score=50.0)
        apply_news_scoring(result)

        assert result.safety_score == 50.0
        assert "NEWS_BEARISH_HIGH_ATTENTION" not in result.risk_flags

    def test_no_news_data_is_noop(self) -> None:
        """No scanned news_sentiment → nothing changes."""
        result = _result(modules_run=["news_sentiment"])
        compute_score(result)
        before = result.safety_score
        apply_news_scoring(result)
        assert result.safety_score == before
        assert result.risk_flags == []

    def test_neutral_sentiment_no_bonus_penalty(self) -> None:
        """NEUTRAL + high attention → no score change (sentiment has no signal)."""
        result = self._news_result(sentiment="NEUTRAL", trending=90, score=50.0)
        apply_news_scoring(result)
        assert result.safety_score == 50.0
        assert "NEWS_BEARISH_HIGH_ATTENTION" not in result.risk_flags
        assert "NEWS_BULLISH_HIGH_ATTENTION" not in result.green_flags

    def test_corroborated_on_chain_evidence_boosts_confidence(self) -> None:
        """3+ sources + on-chain evidence → +5 confidence."""
        result = self._news_result(sentiment="BEARISH", trending=90, score=50.0, total_sources=3, on_chain=True)
        base_confidence = result.confidence
        apply_news_scoring(result)
        assert result.confidence == base_confidence + 5

    def test_weak_corroboration_no_confidence_boost(self) -> None:
        """Fewer than 3 sources (or no on-chain evidence) → no confidence boost."""
        result = self._news_result(sentiment="BULLISH", trending=90, score=50.0, total_sources=2, on_chain=True)
        base_confidence = result.confidence
        apply_news_scoring(result)
        assert result.confidence == base_confidence

        result2 = self._news_result(sentiment="BULLISH", trending=90, score=50.0, total_sources=3, on_chain=False)
        base2 = result2.confidence
        apply_news_scoring(result2)
        assert result2.confidence == base2
