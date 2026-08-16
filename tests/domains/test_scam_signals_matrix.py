"""Tests for the consolidated Scam Signals risk matrix.

Verifies ``_scam_signal_rows`` / ``_format_scam_signals_section`` render a
complete 16-technique coverage matrix with an honest 🔴 / ✅ / — state per
technique — a danger signal never renders as clean, and an absent data
source never renders as "checked clean" (keep-it-honest rule).
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.formatters._scan_report_common import (
    _format_scam_signals_section,
    _scam_signal_rows,
)

_GREEN = "\u2705"
_RED = "\U0001f534"
_UNKNOWN = "\u2014"

_LABELS = [
    "Honeypot (sell-block)",
    "Rug / LP removal",
    "Bundle / sniper",
    "Same-slot bundler",
    "Funding-chain",
    "Fake volume (wash)",
    "Deployer dump",
    "Mint authority",
    "Freeze authority",
    "Tax manipulation",
    "Proxy / upgradeable",
    "Owner-renounce lies",
    "Address poisoning / dusting",
    "Drainer / known scam",
    "Token-2022 extensions",
    "Cloned contract",
]


def _base() -> dict:
    """Minimal report dict (empty evidence -> every technique renders —)."""
    return {
        "address": "0xabc",
        "chain": "ethereum",
        "name": "Test",
        "symbol": "TST",
        "risk_score": 50,
        "honeypot": None,
        "evidence": {"security": {}},
        "bundle_analysis": None,
        "wash_trading": {"fake_volume_pct": None},
        "threats": [],
        "socials": {},
    }


class TestCoverage:
    def test_lists_all_16_techniques(self) -> None:
        labels = [label for label, _state, _detail in _scam_signal_rows(_base())]
        assert labels == _LABELS

    def test_no_data_renders_unknown_dash(self) -> None:
        states = {state for _label, state, _detail in _scam_signal_rows(_base())}
        assert states == {_UNKNOWN}

    def test_section_always_renders_with_16_rows(self) -> None:
        out = _format_scam_signals_section(_base())
        assert "Scam Signals" in "\n".join(out)
        assert sum(1 for line in out if line.startswith("  ")) == 16

    def test_section_empty_for_empty_report(self) -> None:
        assert _format_scam_signals_section({}) == []
        assert _format_scam_signals_section(None) == []


class TestDangerSignals:
    def test_honeypot_detected_is_red(self) -> None:
        r = _base()
        r["honeypot"] = True
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Honeypot (sell-block)"] == _RED

    def test_honeypot_clean_is_green(self) -> None:
        r = _base()
        r["honeypot"] = False
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Honeypot (sell-block)"] == _GREEN

    def test_lp_unlocked_is_red_with_detail(self) -> None:
        r = _base()
        r["lp_locked"] = 0.1  # 10% locked
        rows = {label: (state, detail) for label, state, detail in _scam_signal_rows(r)}
        assert rows["Rug / LP removal"][0] == _RED
        assert "10% locked" in rows["Rug / LP removal"][1]

    def test_lp_locked_is_green(self) -> None:
        r = _base()
        r["lp_locked"] = 0.9
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Rug / LP removal"] == _GREEN

    def test_bundle_detected_is_red(self) -> None:
        r = _base()
        r["bundle_analysis"] = {"is_bundled": True, "launch_buyers": 7}
        rows = {label: (state, detail) for label, state, detail in _scam_signal_rows(r)}
        assert rows["Bundle / sniper"][0] == _RED
        assert "7" in rows["Bundle / sniper"][1]

    def test_bundle_clean_is_green(self) -> None:
        r = _base()
        r["bundle_analysis"] = {"is_bundled": False, "result": "scanned"}
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Bundle / sniper"] == _GREEN

    def test_gooplus_dump_flag_is_red(self) -> None:
        r = _base()
        r["evidence"]["security"]["deployer_dump_detected"] = True
        r["evidence"]["security"]["deployer_dump_count"] = 3
        rows = {label: (state, detail) for label, state, detail in _scam_signal_rows(r)}
        assert rows["Deployer dump"][0] == _RED
        assert "3" in rows["Deployer dump"][1]

    def test_dusting_flag_is_red(self) -> None:
        r = _base()
        r["evidence"]["security"]["dusting_detected"] = True
        r["evidence"]["security"]["dust_recipients"] = 42
        rows = {label: (state, detail) for label, state, detail in _scam_signal_rows(r)}
        assert rows["Address poisoning / dusting"][0] == _RED
        assert "42" in rows["Address poisoning / dusting"][1]

    def test_mint_and_freeze_authority_flags(self) -> None:
        r = _base()
        r["mintable"] = True
        r["freezable"] = True
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Mint authority"] == _RED
        assert states["Freeze authority"] == _RED

    def test_owner_renounce_lie_via_hidden_owner(self) -> None:
        r = _base()
        r["evidence"]["security"]["hidden_owner"] = True
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Owner-renounce lies"] == _RED

    def test_owner_renounced_clean_is_green(self) -> None:
        r = _base()
        r["evidence"]["security"]["owner_renounced"] = True
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Owner-renounce lies"] == _GREEN

    def test_proxy_flag(self) -> None:
        r = _base()
        r["evidence"]["security"]["is_proxy"] = True
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Proxy / upgradeable"] == _RED

    def test_tax_manipulation_high_tax_is_red(self) -> None:
        r = _base()
        r["buy_tax"] = 0.25
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Tax manipulation"] == _RED

    def test_transfer_pausable_is_red(self) -> None:
        r = _base()
        r["evidence"]["security"]["transfer_pausable"] = True
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Tax manipulation"] == _RED

    def test_known_scammer_is_red(self) -> None:
        r = _base()
        r["risk_signals"] = {"known_scammer": True}
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Drainer / known scam"] == _RED

    def test_copycat_flag_is_red(self) -> None:
        r = _base()
        r["threats"] = ["copycat_token"]
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Cloned contract"] == _RED

    def test_fake_volume_is_red(self) -> None:
        r = _base()
        r["wash_trading"] = {"fake_volume_pct": 80.0}
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Fake volume (wash)"] == _RED

    def test_same_slot_bundler_red_when_3_plus(self) -> None:
        r = _base()
        r["bundle_analysis"] = {"is_bundled": False, "same_slot_wallets": 5}
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Same-slot bundler"] == _RED

    def test_cex_funding_heavy_is_red(self) -> None:
        r = _base()
        r["exchange_funded_pct"] = 85.0
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Funding-chain"] == _RED

    def test_cex_funding_light_is_green(self) -> None:
        r = _base()
        r["exchange_funded_pct"] = 20.0
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Funding-chain"] == _GREEN

    def test_token_2022_program_flags_red(self) -> None:
        r = _base()
        r["token_program"] = "spl-token-2022"
        states = {label: state for label, state, _d in _scam_signal_rows(r)}
        assert states["Token-2022 extensions"] == _RED


class TestHtmlSafety:
    def test_no_raw_evidence_strings_leak_into_section(self) -> None:
        """User-controlled evidence fields must never appear unescaped.

        The matrix only renders static labels/details plus formatted numbers,
        so attacker-controlled strings from provider payloads (risk labels,
        details) must not leak into the Telegram-HTML output at all.
        """
        r = _base()
        r["bundle_analysis"] = {
            "is_bundled": True,
            "risk_label": "<b>evil</b>",
            "launch_buyers": 4,
        }
        r["evidence"]["security"]["dust_sender"] = "<script>alert(1)</script>"
        out = "\n".join(_format_scam_signals_section(r))
        assert "<b>evil</b>" not in out
        assert "<script>" not in out
