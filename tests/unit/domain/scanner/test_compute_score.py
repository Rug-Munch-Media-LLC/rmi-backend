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
    BLUECHIP_ADDRESSES,
    DIMENSION_WEIGHTS,
    _is_bluechip,
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
        # Honeypot check (HP01) carries the heavy -35 honeypot penalty;
        # the remaining failed check (CR01) contributes -5, and the 2 rug
        # pattern matches contribute -20 (all in the security dimension).
        assert out["breakdown"]["security"]["delta"] == -60.0
        assert out["breakdown"]["deployer"]["delta"] == -25.0
        assert out["breakdown"]["liquidity"]["delta"] == -20.0
        assert "HONEYPOT_DETECTED" in out["breakdown"]["security"]["flags"]
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
        # HP01 is a confirmed-honeypot check → heavy -35 penalty (not -5)
        assert sec["score"] == 35.0 - 35.0  # max 35 - 35 honeypot penalty
        assert sec["delta"] == -35.0
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
        assert "liquidity" in ev
        assert ev["liquidity"]["locked_pct"] == 0
        assert "holders" in ev
        assert ev["holders"]["top_10_pct"] == 92.4
        assert "holder_health" in ev
        assert ev["holder_health"]["gini"] == 0.91
        assert "deployer" in ev
        assert ev["deployer"]["risk_score"] == 88
        assert "market" in ev
        assert ev["market"]["fake_volume_pct"] == 81

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


class TestSentinelScoring:
    """SENTINEL deep-forensic findings feed the unified score (Phase 5).

    Covers ``compute_score``'s SENTINEL block:

      1. Neutral token with no SENTINEL data keeps the baseline (no boost).
      2. A single critical SENTINEL finding penalizes its dimension but
         triggers no boost (corroboration needs 2+ independent signals).
      3. Two+ critical SENTINEL findings apply the capped ``sentinel_boost``.
      4. SENTINEL holder_analysis HHI feeds the holders dimension.
      5. SENTINEL evidence is exposed in the evidence payload.
    """

    def _sentinel_result(self, sentinel: dict) -> ScanResult:
        return _result(modules_run=["sentinel"], sentinel=sentinel)

    def test_no_sentinel_data_keeps_baseline(self) -> None:
        """No SENTINEL payload → baseline 50, zero boost, no SENTINEL flags."""
        result = _result()
        out = compute_score(result)
        assert out["score"] == 50.0
        assert result.sentinel_boost == 0.0
        assert not [f for f in result.risk_flags if f.startswith("SENTINEL")]

    def test_sentinel_payload_without_free_modules_is_noop(self) -> None:
        """An empty/premium-only SENTINEL payload contributes nothing."""
        result = self._sentinel_result({"free": {}, "premium": None})
        out = compute_score(result)
        assert out["score"] == 50.0
        assert result.sentinel_boost == 0.0

    def test_single_critical_finding_penalizes_no_boost(self) -> None:
        """One critical deep-forensic signal moves its dimension; no boost."""
        result = self._sentinel_result(
            {
                "free": {
                    "holder_analysis": {
                        "hhi": 8000,
                        "risk_level": "critical",
                        "fake_diversification_score": 80,
                        "total_holders": 10,
                    }
                }
            }
        )
        out = compute_score(result)
        assert result.sentinel_boost == 0.0
        assert "SENTINEL_HHI_CRITICAL" in out["breakdown"]["holders"]["flags"]
        assert "SENTINEL_FAKE_DIVERSIFICATION" in out["breakdown"]["holders"]["flags"]
        assert out["breakdown"]["holders"]["delta"] == -15.0
        assert not [f for f in result.risk_flags if f.startswith("SENTINEL_CRITICAL_BOOST")]

    def test_two_critical_findings_apply_boost(self) -> None:
        """Two independent critical modules → sentinel_boost penalty, capped at 15."""
        result = self._sentinel_result(
            {
                "free": {
                    "holder_analysis": {"hhi": 8000, "risk_level": "critical", "total_holders": 10},
                    "liquidity_verification": {
                        "total_locked_percentage": 10,
                        "risk_level": "CRITICAL",
                        "fake_lock_detected": True,
                        "warnings": ["fake lock"],
                    },
                }
            }
        )
        out = compute_score(result)
        assert result.sentinel_boost == 10.0  # 2 critical x 5
        assert "SENTINEL_CRITICAL_BOOST:2" in out["breakdown"]["security"]["flags"]
        assert out["breakdown"]["security"]["delta"] == -10.0

    def test_boost_capped_at_fifteen(self) -> None:
        """Four critical modules → boost capped at 15 (not 20)."""
        result = self._sentinel_result(
            {
                "free": {
                    "holder_analysis": {"hhi": 8000, "risk_level": "critical", "total_holders": 10},
                    "liquidity_verification": {
                        "total_locked_percentage": 10,
                        "risk_level": "CRITICAL",
                        "fake_lock_detected": True,
                        "warnings": ["fake lock"],
                    },
                    "dev_reputation": {"reputation_score": 15, "dev_threat_index": 0.9, "total_launches": 5},
                    "contract_authority": {"risk_score": 95, "risk_level": "CRITICAL"},
                }
            }
        )
        compute_score(result)
        assert result.sentinel_boost == 15.0
        assert "SENTINEL_CRITICAL_BOOST:4" in result.risk_flags

    def test_liquidity_lock_and_dev_threat_penalize_their_dimensions(self) -> None:
        """SENTINEL LP-lock + dev-threat findings land in liquidity/deployer."""
        result = self._sentinel_result(
            {
                "free": {
                    "liquidity_verification": {
                        "total_locked_percentage": 20,
                        "risk_level": "HIGH",
                        "fake_lock_detected": False,
                        "warnings": ["low lock"],
                    },
                    "dev_reputation": {"reputation_score": 20, "dev_threat_index": 0.6, "total_launches": 3},
                }
            }
        )
        out = compute_score(result)
        assert "SENTINEL_LP_LOCK" in out["breakdown"]["liquidity"]["flags"]
        assert "SENTINEL_DEV_THREAT" in out["breakdown"]["deployer"]["flags"]

    def test_sentinel_evidence_exposed(self) -> None:
        """Evidence carries SENTINEL module summaries and the boost."""
        result = self._sentinel_result(
            {
                "free": {
                    "holder_analysis": {"hhi": 8000, "risk_level": "critical", "total_holders": 10},
                    "liquidity_verification": {
                        "total_locked_percentage": 10,
                        "risk_level": "CRITICAL",
                        "fake_lock_detected": True,
                        "warnings": ["fake lock"],
                    },
                }
            }
        )
        out = compute_score(result)
        ev_sentinel = out["evidence"]["sentinel"]
        assert "holder_analysis" in ev_sentinel
        assert ev_sentinel["holder_analysis"]["hhi"] == 8000
        assert "liquidity_verification" in ev_sentinel
        assert ev_sentinel["sentinel_boost"] == 10.0

    def test_clean_sentinel_signals_are_green(self) -> None:
        """Strong LP lock + clean dev reputation add small bonuses."""
        result = self._sentinel_result(
            {
                "free": {
                    "liquidity_verification": {
                        "total_locked_percentage": 95,
                        "risk_level": "LOW",
                        "fake_lock_detected": False,
                        "warnings": [],
                    },
                    "dev_reputation": {"reputation_score": 90, "dev_threat_index": 0.0, "total_launches": 8},
                }
            }
        )
        out = compute_score(result)
        assert "SENTINEL_LP_LOCKED" in out["breakdown"]["liquidity"]["flags"]
        assert "SENTINEL_DEV_CLEAN" in out["breakdown"]["deployer"]["flags"]


class TestBlueChipGuard:
    """Bug 1: SENTINEL/heuristic flags must not mark blue-chips (WETH) HIGH.

    Wrapped/blue-chip tokens legitimately score high HHI and low locked-LP%
    (one wrapping contract dominates supply; liquidity sits in deep AMM
    pools, not locked LP positions). ``compute_score`` skips those flags
    when the token is top-100 by market cap or has >100K holders.
    """

    def _bluechip(self, **overrides: object) -> ScanResult:
        """A WETH-shaped token: huge market cap + concentrated/locked-LP signals."""
        base: dict[str, object] = {
            "name": "Wrapped Ether",
            "symbol": "WETH",
            "market_cap_usd": 50_000_000_000.0,
            "holders": {"top_10_pct": 85, "holder_count": 340_000},
            "holder_health": {"gini": 0.85, "hhi": 8000},
            "liquidity": {"locked_pct": 5},
            "sentinel": {
                "free": {
                    "holder_analysis": {"hhi": 8000, "risk_level": "critical", "total_holders": 340_000},
                    "liquidity_verification": {
                        "total_locked_percentage": 5,
                        "risk_level": "HIGH",
                        "fake_lock_detected": False,
                        "warnings": ["low lock"],
                    },
                }
            },
        }
        base.update(overrides)
        return _result(**base)

    def test_bluechip_market_cap_skips_sentinel_and_baseline_flags(self) -> None:
        """A $50B token with HHI/lock signals must not be flagged HIGH."""
        out = compute_score(self._bluechip())
        assert "SENTINEL_HHI_CRITICAL" not in out["breakdown"]["holders"]["flags"]
        assert "SENTINEL_HHI_HIGH" not in out["breakdown"]["holders"]["flags"]
        assert "SENTINEL_LP_LOCK" not in out["breakdown"]["liquidity"]["flags"]
        assert "LP_LOCK_LOW" not in out["breakdown"]["liquidity"]["flags"]
        assert "HOLDER_CONCENTRATION_HIGH" not in out["breakdown"]["holders"]["flags"]
        assert "HOLDER_GINI_HIGH" not in out["breakdown"]["holders"]["flags"]
        assert out["score"] >= 50.0
        assert out["risk_level"] in ("safe", "low", "medium")

    def test_bluechip_holder_count_skips_flags(self) -> None:
        """>100K holders alone (no market cap) also triggers the guard."""
        out = compute_score(
            self._bluechip(
                market_cap_usd=0.0,
                holders={"top_10_pct": 90, "holder_count": 250_000},
            )
        )
        assert "SENTINEL_HHI_CRITICAL" not in out["breakdown"]["holders"]["flags"]
        assert "SENTINEL_LP_LOCK" not in out["breakdown"]["liquidity"]["flags"]

    def test_bluechip_via_sentinel_total_holders(self) -> None:
        """SENTINEL holder_analysis.total_holders (>100K) triggers the guard."""
        out = compute_score(
            self._bluechip(
                market_cap_usd=0.0,
                holders=None,
                sentinel={
                    "free": {
                        "holder_analysis": {"hhi": 9000, "risk_level": "critical", "total_holders": 500_000},
                        "liquidity_verification": {
                            "total_locked_percentage": 10,
                            "risk_level": "HIGH",
                            "fake_lock_detected": False,
                            "warnings": [],
                        },
                    }
                },
            )
        )
        assert "SENTINEL_HHI_CRITICAL" not in out["breakdown"]["holders"]["flags"]
        assert "SENTINEL_LP_LOCK" not in out["breakdown"]["liquidity"]["flags"]

    def test_bluechip_still_flags_real_fake_lock(self) -> None:
        """A fake-lock finding is a real scam signal even for bluechips."""
        out = compute_score(
            self._bluechip(
                sentinel={
                    "free": {
                        "liquidity_verification": {
                            "total_locked_percentage": 80,
                            "risk_level": "CRITICAL",
                            "fake_lock_detected": True,
                            "warnings": ["fake lock"],
                        },
                    }
                }
            )
        )
        assert "SENTINEL_LP_LOCK" in out["breakdown"]["liquidity"]["flags"]

    def test_bluechip_allowlist_matches_no_data_token(self) -> None:
        """Hardcoded allowlist (WETH) is blue-chip even with zero market data.

        Regression for the data-fetch OOM case: WETH scanned with no
        market-cap / holder enrichment must still bypass SENTINEL flags.
        """
        assert "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2" in BLUECHIP_ADDRESSES
        r = ScanResult(
            token_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            chain="ethereum",
            market_cap_usd=0.0,
            holders=None,
        )
        assert _is_bluechip(r) is True
        out = compute_score(
            self._bluechip(
                token_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                market_cap_usd=0.0,
                holders=None,
            )
        )
        assert "SENTINEL_HHI_CRITICAL" not in out["breakdown"]["holders"]["flags"]
        assert "SENTINEL_LP_LOCK" not in out["breakdown"]["liquidity"]["flags"]
        assert out["risk_level"] in ("safe", "low", "medium")

    def test_bluechip_allowlist_is_case_insensitive(self) -> None:
        """Lowercased WETH address (as returned by many providers) still matches."""
        r = ScanResult(
            token_address="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
            chain="ethereum",
            market_cap_usd=0.0,
        )
        assert _is_bluechip(r) is True

    def test_unknown_token_not_bluechip_without_data(self) -> None:
        """A random address with no data must NOT be treated as blue-chip."""
        r = ScanResult(
            token_address="0xDEADBEEF00000000000000000000000000000000",
            chain="ethereum",
            market_cap_usd=0.0,
            holders=None,
        )
        assert _is_bluechip(r) is False

    def test_non_bluechip_still_flagged(self) -> None:
        """A small-cap token with the same signals keeps the penalties."""
        out = compute_score(
            self._bluechip(
                market_cap_usd=2_000_000.0,
                holders={"top_10_pct": 85, "holder_count": 400},
                sentinel={
                    "free": {
                        "holder_analysis": {"hhi": 8000, "risk_level": "critical", "total_holders": 400},
                        "liquidity_verification": {
                            "total_locked_percentage": 5,
                            "risk_level": "HIGH",
                            "fake_lock_detected": False,
                            "warnings": ["low lock"],
                        },
                    }
                },
            )
        )
        assert "SENTINEL_HHI_CRITICAL" in out["breakdown"]["holders"]["flags"]
        assert "SENTINEL_LP_LOCK" in out["breakdown"]["liquidity"]["flags"]
        assert "LP_LOCK_LOW" in out["breakdown"]["liquidity"]["flags"]
        assert "HOLDER_CONCENTRATION_HIGH" in out["breakdown"]["holders"]["flags"]
