"""Pure unit tests for the upgraded ``format_scan_report`` layout.

Verifies the top-tier report structure: RugMunch verdict header, a single
consolidated Advanced Analysis section, exactly one wallet-freshness line,
no duplicate section headings, and the canonical section order
(verdict → security → market → sentinel → holders → deployer →
advanced → socials → footer). Pure — no network, DB, or Telegram access.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.formatters._sentinel import format_scan_report


def _sample_report() -> dict:
    """Rich sample scan report exercising every section of the layout."""
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
        "honeypot": False,
        "contract_verified": True,
        "mintable": True,
        "owner_renounced": False,
        "lp_locked": 0.8,
        "buy_tax": 0.05,
        "sell_tax": 0.07,
        "bundle_analysis": {"is_bundled": True, "confidence": 87.0, "risk_label": "high"},
        "volume_real_ratio": 3.5,
        "deployer_address": "0xdeployer",
        "dev_wallet": "0xdevwallet",
        "deployer_profile": {
            "total_tokens_launched": 15,
            "rugged_pct": 0.4,
            "avg_lifespan_days": 3.0,
        },
        "wallet_metrics": {
            "result": "scanned",
            "holders_analyzed": 25,
            "fresh_wallet_pct": 22.5,
            "cex_funded_pct": 45.0,
            "first_tx_pct": 12.0,
        },
        "sentinel": {
            "free": {
                "holder_analysis": {
                    "hhi": 4200,
                    "risk_level": "high",
                    "warnings": ["HI01: Extreme holder concentration"],
                },
                "liquidity_verification": {
                    "total_locked_percentage": 45.0,
                    "warnings": ["MD01: LP lock below 6 months"],
                },
                "contract_authority": {"risk_level": "LOW"},
                "proxy_detection": {"is_proxy": False, "can_upgrade": False},
                "metadata_fingerprint": {"warnings": []},
                "dev_reputation": {"reputation_level": "safe", "reputation_score": 85},
            }
        },
        "top_holders": [
            {"address": "0xaaa", "pct": 40.0, "tag": "Dev", "is_contract": False},
            {"address": "0xbbb", "pct": 20.0, "tag": None, "is_contract": True},
        ],
        "socials": {"twitter": "https://x.com/tst", "telegram": "https://t.me/tst"},
        "threats": [],
    }


class TestVerdictHeader:
    def test_rugmunch_brand_header(self) -> None:
        out = format_scan_report(_sample_report())
        assert "🛡️ <b>Rug Munch Token Scan</b>" in out

    def test_big_risk_score_and_verdict(self) -> None:
        out = format_scan_report(_sample_report())
        assert "60/100" in out
        # Verdict word comes from _base.format_verdict_header when present
        # (CAUTION for 60), otherwise the inline fallback (HIGH RISK).
        assert ("<b>CAUTION</b>" in out) or ("<b>HIGH RISK</b>" in out)
        # One-line verdict sentence names the single most important finding.
        assert "Supply is mintable" in out

    def test_token_identity_present(self) -> None:
        out = format_scan_report(_sample_report())
        assert "<b>Test Token</b> (TST)" in out
        assert "<code>0xabc123</code>" in out

    def test_bundle_alert_prominent_after_verdict(self) -> None:
        out = format_scan_report(_sample_report())
        assert "BUNDLED LAUNCH DETECTED" in out
        assert out.index("BUNDLED LAUNCH DETECTED") < out.index("Security Assessment")
        # Banner suppresses the legacy one-liner — no double bundle report.
        assert "Bundle activity detected" not in out


class TestSingleSections:
    def test_exactly_one_advanced_analysis_heading(self) -> None:
        out = format_scan_report(_sample_report())
        assert out.count("Advanced Analysis") == 1
        assert out.count("<b>Advanced Analysis</b>") == 1

    def test_exactly_one_wallet_freshness_line(self) -> None:
        out = format_scan_report(_sample_report())
        assert out.count("fresh (<30d)") == 1
        assert "22% fresh (<30d)" in out
        # Legacy fallback is not emitted alongside the helper line.
        assert "Fresh wallets:" not in out

    def test_no_duplicate_headings(self) -> None:
        out = format_scan_report(_sample_report())
        for heading in (
            "Security Assessment",
            "Market Data",
            "AEGIS Forensics",
            "Top Holders",
            "<b>Deployer</b>",
            "Advanced Analysis",
            "Socials",
        ):
            assert out.count(heading) == 1, f"expected exactly one heading: {heading}"


class TestSectionOrder:
    def test_verdict_before_market_before_holders_before_footer(self) -> None:
        out = format_scan_report(_sample_report())
        idx_verdict = out.index("<b>Rug Munch Token Scan</b>")
        idx_market = out.index("Market Data")
        idx_holders = out.index("Top Holders")
        idx_footer = out.index("Trade:")
        assert idx_verdict < idx_market < idx_holders < idx_footer

    def test_full_section_order(self) -> None:
        out = format_scan_report(_sample_report())
        order = [
            "<b>Rug Munch Token Scan</b>",
            "Security Assessment",
            "Market Data",
            "AEGIS Forensics",
            "Top Holders",
            "<b>Deployer</b>",
            "Advanced Analysis",
            "Socials",
            "Trade:",
        ]
        positions = [out.index(name) for name in order]
        assert positions == sorted(positions), f"section order violated: {dict(zip(order, positions, strict=True))}"


class TestLayoutPolish:
    def test_no_double_blank_lines_between_sections(self) -> None:
        # v12: a red-alert banner followed by the next section header used to
        # render a double blank line — sections must stay single-spaced.
        out = format_scan_report(_sample_report())
        assert "\n\n\n" not in out, "no triple-newline runs in scan output"

    def test_sentinel_metric_values_bold(self) -> None:
        # v12: sentinel flag metric values render bold like the other /scan
        # sections (HHI / LP / risk levels), not plain text.
        out = format_scan_report(_sample_report())
        assert "HHI <b>4200</b>" in out
        assert "LP Locked: <b>45%</b>" in out
        assert "Contract authority risk: <b>LOW</b>" not in out  # LOW not flagged


class TestDeployerInOwnSection:
    def test_repeat_deployer_line_lives_in_deployer_section(self) -> None:
        out = format_scan_report(_sample_report())
        assert "<b>Deployer</b>" in out
        assert "15 tokens" in out
        assert "40% rugged" in out
        assert "avg life 3 days" in out
        # The compact summary is inside the Deployer section, before Advanced.
        assert out.index("Repeat Deployer") < out.index("Advanced Analysis")

    def test_dev_wallet_line_only_when_distinct_from_deployer(self) -> None:
        out = format_scan_report(_sample_report())
        # deployer_address (0xdeployer) differs from dev_wallet (0xdevwallet).
        assert "Dev: <code>0xdevwallet</code>" in out

        report = _sample_report()
        report["dev_wallet"] = report["deployer_address"]  # same wallet — deduped
        out_same = format_scan_report(report)
        assert "Dev: <code>0xdevwallet</code>" not in out_same
        assert "<code>0xdeployer</code>" in out_same
