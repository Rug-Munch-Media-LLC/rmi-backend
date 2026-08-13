"""Tests for the RugMunch bot risk/verdict rendering helpers.

Covers the risk formatting core in
``app/domains/telegram/rugmunchbot/formatters/_base.py``:

* ``format_verdict_header`` — big color-coded scan headline
* ``_risk_indicator`` — 0-100 score → risk emoji
* ``format_scoring_breakdown`` — explainable per-dimension bars
* ``format_funding_analysis`` — exchange/fresh/overlap thresholds

All tests are pure (no network, no DB).
"""

from __future__ import annotations

import pytest

from app.domains.telegram.rugmunchbot.formatters._base import (
    RISK_BAR_EMPTY,
    RISK_BAR_FILLED,
    RISK_EMOJI,
    RISK_VERDICT,
    _risk_indicator,
    _risk_level,
    format_funding_analysis,
    format_scoring_breakdown,
    format_verdict_header,
)

# --- format_verdict_header -------------------------------------------------


class TestFormatVerdictHeader:
    def test_high_risk_score(self) -> None:
        out = format_verdict_header({"risk_score": 82})
        assert "🔴 <b>HIGH RISK</b> — Score 82/100" in out
        assert out.count("\n") == 1

    def test_medium_risk_score(self) -> None:
        out = format_verdict_header({"risk_score": 55})
        assert "🟡 <b>CAUTION</b> — Score 55/100" in out

    def test_low_risk_score(self) -> None:
        out = format_verdict_header({"risk_score": 12})
        assert "🟢 <b>LOW RISK</b> — Score 12/100" in out

    @pytest.mark.parametrize(
        ("score", "emoji", "verdict"),
        [
            (70, "🔴", "HIGH RISK"),
            (69, "🟡", "CAUTION"),
            (40, "🟡", "CAUTION"),
            (39, "🟢", "LOW RISK"),
            (0, "🟢", "LOW RISK"),
        ],
    )
    def test_score_threshold_boundaries(self, score: int, emoji: str, verdict: str) -> None:
        out = format_verdict_header({"risk_score": score})
        assert f"{emoji} <b>{verdict}</b> — Score {score}/100" in out

    def test_honeypot_sentence_wins_priority(self) -> None:
        out = format_verdict_header({"risk_score": 90, "honeypot": True, "mintable": True})
        assert "Honeypot detected" in out
        assert "mintable" not in out.lower()

    def test_mintable_sentence(self) -> None:
        out = format_verdict_header({"risk_score": 55, "mintable": True})
        assert "Supply is mintable" in out

    def test_ownership_not_renounced_sentence(self) -> None:
        out = format_verdict_header({"risk_score": 40, "owner_renounced": False})
        assert "Ownership is not renounced" in out

    def test_renounced_true_does_not_trigger_sentence(self) -> None:
        out = format_verdict_header({"risk_score": 12, "owner_renounced": True})
        assert "No critical flags" in out

    def test_low_liquidity_sentence(self) -> None:
        out = format_verdict_header({"risk_score": 45, "liquidity": 5_000})
        assert "Liquidity is thin" in out

    def test_decent_liquidity_no_sentence(self) -> None:
        out = format_verdict_header({"risk_score": 12, "liquidity": 500_000})
        assert "No critical flags" in out

    def test_no_critical_flags_sentence(self) -> None:
        out = format_verdict_header({"risk_score": 12, "honeypot": False, "mintable": False, "owner_renounced": True})
        assert "No critical flags" in out

    def test_missing_risk_score_renders_unknown(self) -> None:
        out = format_verdict_header({})
        assert "⚪ <b>UNKNOWN</b>" in out
        assert "no risk score available" in out

    def test_missing_keys_does_not_crash(self) -> None:
        # A bare dict must never raise and must stay HTML-safe.
        out = format_verdict_header({"risk_score": None})
        assert isinstance(out, str)
        assert "**" not in out
        out2 = format_verdict_header({"risk_score": "garbage"})
        assert "⚪ <b>UNKNOWN</b>" in out2

    def test_honeypot_only_payload(self) -> None:
        # No risk_score but a honeypot flag: headline is UNKNOWN, sentence
        # still names the finding.
        out = format_verdict_header({"honeypot": True})
        assert "⚪ <b>UNKNOWN</b>" in out
        assert "Honeypot detected" in out

    def test_html_safe_no_raw_markdown(self) -> None:
        out = format_verdict_header({"risk_score": 82, "mintable": True})
        assert "<b>" in out
        assert "**" not in out


# --- _risk_indicator / _risk_level ----------------------------------------


class TestRiskIndicator:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [(95, "🔴"), (70, "🔴"), (69, "🟡"), (40, "🟡"), (39, "🟢"), (0, "🟢")],
    )
    def test_mapping(self, score: int, expected: str) -> None:
        assert _risk_indicator(score) == expected

    def test_matches_level_helper(self) -> None:
        for score in (100, 50, 0):
            assert _risk_indicator(score) == RISK_EMOJI[_risk_level(score)]

    def test_single_source_of_truth(self) -> None:
        # The emoji constants are the source; the helper must not hardcode.
        assert RISK_EMOJI["high"] == "🔴"
        assert RISK_EMOJI["medium"] == "🟡"
        assert RISK_EMOJI["low"] == "🟢"
        assert RISK_VERDICT == {"high": "HIGH RISK", "medium": "CAUTION", "low": "LOW RISK"}


# --- format_scoring_breakdown ---------------------------------------------


class TestFormatScoringBreakdown:
    def test_empty_breakdown(self) -> None:
        assert format_scoring_breakdown({}) == ""
        assert format_scoring_breakdown(None) == ""

    def test_bar_widths_scale_with_score(self) -> None:
        out = format_scoring_breakdown(
            {
                "security": {"score": 100, "max": 100, "delta": 0},
                "liquidity": {"score": 50, "max": 100, "delta": 0},
                "holders": {"score": 0, "max": 100, "delta": 0},
            }
        )
        security_line = next(line for line in out.splitlines() if "Contract Security" in line)
        liquidity_line = next(line for line in out.splitlines() if "Liquidity" in line)
        holders_line = next(line for line in out.splitlines() if "Holders" in line)
        assert security_line.count(RISK_BAR_FILLED) == 10
        assert security_line.count(RISK_BAR_EMPTY) == 0
        assert liquidity_line.count(RISK_BAR_FILLED) == 5
        assert liquidity_line.count(RISK_BAR_EMPTY) == 5
        assert holders_line.count(RISK_BAR_FILLED) == 0
        assert holders_line.count(RISK_BAR_EMPTY) == 10

    def test_risk_emoji_per_dimension(self) -> None:
        out = format_scoring_breakdown(
            {
                "security": {"score": 82, "max": 100, "delta": 0},
                "liquidity": {"score": 55, "max": 100, "delta": 0},
                "holders": {"score": 10, "max": 100, "delta": 0},
            }
        )
        security_line = next(line for line in out.splitlines() if "Contract Security" in line)
        liquidity_line = next(line for line in out.splitlines() if "Liquidity" in line)
        holders_line = next(line for line in out.splitlines() if "Holders" in line)
        assert security_line.startswith("  🔴 ")
        assert liquidity_line.startswith("  🟡 ")
        assert holders_line.startswith("  🟢 ")

    def test_delta_arrows(self) -> None:
        out = format_scoring_breakdown(
            {
                "security": {"score": 80, "max": 100, "delta": 5},
                "liquidity": {"score": 80, "max": 100, "delta": -3},
                "holders": {"score": 80, "max": 100, "delta": 0},
            }
        )
        lines = out.splitlines()
        security_line = next(line for line in lines if "Contract Security" in line)
        liquidity_line = next(line for line in lines if "Liquidity" in line)
        holders_line = next(line for line in lines if "Holders" in line)
        assert security_line.endswith("▲")
        assert liquidity_line.endswith("▼")
        assert not holders_line.endswith("▲")
        assert not holders_line.endswith("▼")

    def test_bold_values_and_flag_lines(self) -> None:
        out = format_scoring_breakdown(
            {"security": {"score": 82, "max": 100, "delta": 0, "flags": ["bad thing", "worse thing", "hidden"]}}
        )
        assert "<b>82/100</b>" in out
        assert "bad thing" in out
        assert "worse thing" in out
        assert "hidden" not in out  # flags capped at 2

    def test_max_zero_does_not_crash(self) -> None:
        out = format_scoring_breakdown({"security": {"score": 0, "max": 0, "delta": 0}})
        assert "<b>0/0</b>" in out
        assert RISK_BAR_EMPTY * 10 in out

    def test_html_safe(self) -> None:
        out = format_scoring_breakdown({"security": {"score": 50, "max": 100, "delta": 0}})
        assert "**" not in out


# --- format_funding_analysis ----------------------------------------------


class TestFormatFundingAnalysis:
    def test_empty_payload(self) -> None:
        assert format_funding_analysis({}) == ""
        assert format_funding_analysis({"foo": 1}) == ""

    def test_exchange_thresholds(self) -> None:
        assert "🔴 Exchange-Funded" in format_funding_analysis({"exchange_funded_pct": 60})
        assert "🔴 Exchange-Funded" in format_funding_analysis({"exchange_funded_pct": 51})
        assert "🟡 Exchange-Funded" in format_funding_analysis({"exchange_funded_pct": 50})
        assert "🟡 Exchange-Funded" in format_funding_analysis({"exchange_funded_pct": 31})
        assert "🟢 Exchange-Funded" in format_funding_analysis({"exchange_funded_pct": 10})

    def test_fresh_wallet_thresholds(self) -> None:
        assert "🔴 Fresh Wallets" in format_funding_analysis({"fresh_wallet_pct": 41})
        assert "🟡 Fresh Wallets" in format_funding_analysis({"fresh_wallet_pct": 40})
        assert "🟡 Fresh Wallets" in format_funding_analysis({"fresh_wallet_pct": 21})
        assert "🟢 Fresh Wallets" in format_funding_analysis({"fresh_wallet_pct": 20})

    def test_overlap_thresholds(self) -> None:
        assert "🔴 Holder Overlap" in format_funding_analysis({"holder_overlap_pct": 31})
        assert "🟡 Holder Overlap" in format_funding_analysis({"holder_overlap_pct": 30})
        assert "🟡 Holder Overlap" in format_funding_analysis({"holder_overlap_pct": 11})
        assert "🟢 Holder Overlap" in format_funding_analysis({"holder_overlap_pct": 10})

    def test_combined_thresholds(self) -> None:
        out = format_funding_analysis({"exchange_funded_pct": 60, "fresh_wallet_pct": 30, "holder_overlap_pct": 5})
        assert "🔴 Exchange-Funded" in out
        assert "🟡 Fresh Wallets" in out
        assert "🟢 Holder Overlap" in out

    def test_missing_values_render_na(self) -> None:
        out = format_funding_analysis({"exchange_funded_pct": 60})
        assert "⚪ Exchange-Funded" not in out
        assert "⚪ Fresh Wallets:           <b>N/A</b>" in out
        assert "⚪ Holder Overlap w/ Scams: <b>N/A</b>" in out

    def test_bold_values_and_no_raw_markdown(self) -> None:
        out = format_funding_analysis({"exchange_funded_pct": 60})
        assert "<b>60%</b>" in out
        assert "**" not in out
