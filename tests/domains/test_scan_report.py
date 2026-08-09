"""Unit tests for rugmunchbot /scan report formatting (formatters.py).

Covers the new own-data intel blocks wired into ``format_scan_report``:
bundle-launch red alert, repeat-deployer history, and wallet freshness /
CEX-funding metrics. Verifies each section renders when the scanner output
carries the signal and is skipped when absent. No network / Telegram calls.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.formatters import (
    _bundle_alert_line,
    _deployer_history_line,
    _wallet_freshness_line,
    format_scan_report,
)


def _base_report() -> dict:
    """Minimal legacy scan report dict (every key format_scan_report reads)."""
    return {
        "address": "0xabc123",
        "chain": "ethereum",
        "name": "Test Token",
        "symbol": "TST",
        "price": 1.0,
        "mcap": 1_000_000,
        "liquidity": 50_000,
        "volume_24h": 100_000,
        "price_change_1h": 1.0,
        "price_change_24h": 5.0,
        "price_change_7d": 10.0,
        "risk_score": 60,
        "honeypot": None,
        "contract_verified": None,
        "mintable": None,
        "owner_renounced": None,
        "lp_locked": None,
        "buy_tax": None,
        "sell_tax": None,
        "top_holders": [],
        "bundle_detected": False,
        "fresh_wallet_pct": 0,
        "volume_real_ratio": None,
        "dev_wallet": None,
        "socials": {},
        "threats": [],
    }


class TestBundleAlert:
    def test_renders_red_banner_when_bundled(self) -> None:
        report = _base_report()
        report["bundle_analysis"] = {"is_bundled": True, "confidence": 87.0, "risk_label": "high"}
        out = format_scan_report(report)
        assert "🚨 <b>BUNDLED LAUNCH DETECTED</b>" in out
        assert "87% confidence" in out
        assert "HIGH" in out

    def test_helper_none_when_not_bundled(self) -> None:
        report = _base_report()
        report["bundle_analysis"] = {"is_bundled": False, "confidence": 12.0, "risk_label": "low"}
        assert _bundle_alert_line(report) is None

    def test_skipped_when_bundle_analysis_absent(self) -> None:
        out = format_scan_report(_base_report())
        assert "BUNDLED LAUNCH DETECTED" not in out

    def test_helper_none_when_bundle_analysis_malformed(self) -> None:
        report = _base_report()
        report["bundle_analysis"] = None
        assert _bundle_alert_line(report) is None
        report["bundle_analysis"] = "not-a-dict"
        assert _bundle_alert_line(report) is None

    def test_suppresses_legacy_bundle_line_when_banner_present(self) -> None:
        report = _base_report()
        report["bundle_detected"] = True  # legacy signal
        report["bundle_analysis"] = {"is_bundled": True, "confidence": 60.0, "risk_label": "medium"}
        out = format_scan_report(report)
        # New banner renders, legacy one-liner is suppressed (no double report).
        assert "BUNDLED LAUNCH DETECTED" in out
        assert "Bundle activity detected" not in out


class TestDeployerHistory:
    def test_renders_repeat_deployer_line(self) -> None:
        report = _base_report()
        report["deployer_profile"] = {
            "total_tokens_launched": 15,
            "total_deployments": 18,
            "rugged_pct": 0.4,
            "avg_lifespan_days": 3.0,
        }
        out = format_scan_report(report)
        assert "👤 <b>Repeat Deployer</b>" in out
        assert "15 tokens" in out
        assert "40% rugged" in out
        assert "avg life 3 days" in out

    def test_skipped_when_deployer_profile_absent(self) -> None:
        out = format_scan_report(_base_report())
        assert "Repeat Deployer" not in out

    def test_helper_none_when_profile_empty(self) -> None:
        report = _base_report()
        report["deployer_profile"] = {}
        assert _deployer_history_line(report) is None
        report["deployer_profile"] = None
        assert _deployer_history_line(report) is None

    def test_hours_when_lifespan_under_a_day(self) -> None:
        report = _base_report()
        report["deployer_profile"] = {
            "total_tokens_launched": 5,
            "rugged_pct": 1.0,
            "avg_lifespan_days": 0.5,
        }
        out = format_scan_report(report)
        assert "avg life 12 hours" in out
        assert "100% rugged" in out

    def test_falls_back_to_sentinel_rug_rate_percentage(self) -> None:
        report = _base_report()
        report["deployer_profile"] = {"total_deployments": 7, "rug_rate": 57.0}
        out = format_scan_report(report)
        assert "7 tokens" in out
        assert "57% rugged" in out

    def test_unknown_lifespan(self) -> None:
        report = _base_report()
        report["deployer_profile"] = {"total_tokens_launched": 3, "rugged_pct": 0.2}
        out = format_scan_report(report)
        assert "avg life unknown" in out


class TestWalletFreshness:
    def test_renders_fresh_and_cex_funded(self) -> None:
        report = _base_report()
        report["wallet_metrics"] = {"fresh_wallet_pct": 22.5, "cex_funded_pct": 45.0, "result": "scanned"}
        out = format_scan_report(report)
        assert "22% fresh wallets (<30 days)" in out
        assert "45% CEX-funded" in out

    def test_skipped_when_wallet_metrics_absent(self) -> None:
        out = format_scan_report(_base_report())
        assert "fresh wallets" not in out
        assert "CEX-funded" not in out

    def test_skipped_when_unavailable_degradation_payload(self) -> None:
        report = _base_report()
        report["wallet_metrics"] = {
            "fresh_wallet_pct": 0.0,
            "cex_funded_pct": 0.0,
            "result": "unavailable",
        }
        out = format_scan_report(report)
        assert "fresh wallets" not in out
        assert "CEX-funded" not in out

    def test_helper_none_when_malformed(self) -> None:
        report = _base_report()
        report["wallet_metrics"] = None
        assert _wallet_freshness_line(report) is None
        report["wallet_metrics"] = "nope"
        assert _wallet_freshness_line(report) is None

    def test_replaces_legacy_fresh_wallet_line(self) -> None:
        report = _base_report()
        report["fresh_wallet_pct"] = 99  # legacy field — should be superseded
        report["wallet_metrics"] = {"fresh_wallet_pct": 10.0, "cex_funded_pct": 30.0, "result": "scanned"}
        out = format_scan_report(report)
        assert "10% fresh wallets (<30 days)" in out
        assert "Fresh wallets: 99%" not in out


class TestAllIntelTogether:
    def test_all_sections_render_when_present(self) -> None:
        report = _base_report()
        report["bundle_analysis"] = {"is_bundled": True, "confidence": 92.0, "risk_label": "critical"}
        report["deployer_profile"] = {"total_tokens_launched": 12, "rugged_pct": 0.75, "avg_lifespan_days": 2.0}
        report["wallet_metrics"] = {"fresh_wallet_pct": 35.0, "cex_funded_pct": 20.0, "result": "scanned"}
        out = format_scan_report(report)
        assert "BUNDLED LAUNCH DETECTED" in out
        assert "92% confidence, CRITICAL" in out
        assert "Repeat Deployer" in out
        assert "35% fresh wallets (<30 days)" in out
        assert "20% CEX-funded" in out

    def test_all_sections_skipped_when_absent(self) -> None:
        out = format_scan_report(_base_report())
        assert "BUNDLED LAUNCH DETECTED" not in out
        assert "Repeat Deployer" not in out
        assert "fresh wallets" not in out
        assert "CEX-funded" not in out
