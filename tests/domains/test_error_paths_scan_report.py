"""Error-path tests for the scan report renderers (None-typed scan results).

Task-critical contract: a scan result carrying ``risk_score=None``,
``mcap=None`` or ``liquidity=None`` (provider degradation / delisted pair)
must render the report with honest "—"/"N/A" placeholders — never crash,
never fabricate a 0. Pure formatter tests — no network, no Telegram.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.config import fmt_number, fmt_pct
from app.domains.telegram.rugmunchbot.formatters import format_deep_scan_report, format_scan_report
from app.domains.telegram.rugmunchbot.formatters._scan_report_common import (
    _fmt_market_value,
    _fmt_num,
    _key_metrics_rows,
    _risk_emoji,
)


def _none_report() -> dict[str, Any]:
    """Legacy scan result where every numeric provider field is None."""
    return {
        "address": "0xabc123",
        "chain": "ethereum",
        "name": "Test Token",
        "symbol": "TST",
        "price": None,
        "mcap": None,
        "liquidity": None,
        "volume_24h": None,
        "price_change_1h": None,
        "price_change_24h": None,
        "price_change_7d": None,
        "risk_score": None,
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


class TestScanReportNoneRendering:
    def test_format_scan_report_none_typed_never_crashes(self) -> None:
        report = format_scan_report(_none_report())
        assert "Test Token" in report
        # Risk score renders as N/A (unknown) rather than a fabricated 0.
        assert "N/A" in report
        # Market values render as the em-dash "—" placeholder.
        assert "\u2014" in report

    def test_format_deep_scan_report_none_typed_never_crashes(self) -> None:
        report = format_deep_scan_report(_none_report())
        assert "Test Token" in report
        assert "\u2014" in report

    def test_key_metrics_rows_none_typed(self) -> None:
        rows = _key_metrics_rows(_none_report())
        joined = "\n".join(rows)
        assert "N/A" in joined  # risk + holders
        assert "\u2014" in joined  # liquidity

    def test_none_report_never_fabricates_zero(self) -> None:
        report = format_scan_report(_none_report())
        # "Liquidity $0" would be a fabrication — the report must show the
        # placeholder instead of inventing a zero.
        assert "Liquidity      <b>$0</b>" not in report


class TestFormattingHelpers:
    def test_fmt_market_value_none_is_emdash(self) -> None:
        assert _fmt_market_value(None) == "\u2014"
        assert _fmt_market_value("garbage") == "\u2014"

    def test_fmt_market_value_zero_is_literal_zero(self) -> None:
        """A genuine 0 (delisted pair) renders $0 — honest, not "—"."""
        assert _fmt_market_value(0) == "$0"

    def test_fmt_market_value_real_number(self) -> None:
        assert _fmt_market_value(1500) == "$1.5K"

    def test_fmt_num_none_is_na(self) -> None:
        assert _fmt_num(None) == "N/A"

    def test_risk_emoji_none_is_neutral(self) -> None:
        assert _risk_emoji(None) == "\u26aa"

    def test_config_fmt_helpers_are_na_safe(self) -> None:
        assert fmt_number(None) == "N/A"
        assert fmt_pct(None) == "N/A"
