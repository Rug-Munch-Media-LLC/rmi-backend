"""Pure tests for the user-facing "SENTINEL" → "AEGIS" branding rename.

The Telegram scan report's human-readable branding was renamed from
"SENTINEL" to "AEGIS". Only user-facing render strings changed — all
internal identifiers (``format_sentinel_deepscan_section``,
``format_sentinel_flags``, ``SENTINEL_MODULE_EMOJI``, etc.) are unchanged
and must keep importing/working.

Pure — no network, DB, or Telegram access.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.formatters import (
    _SENTINEL_CODE_MAP,
    SENTINEL_MODULE_EMOJI,
    format_scan_report,
    format_sentinel_deepscan_section,
    format_sentinel_flags,
    format_sentinel_scan_report,
)


def _sample_deepscan() -> dict:
    """Minimal SentinelReport-shaped payload for the deep-scan formatter."""
    return {
        "address": "0xabc123",
        "chain": "ethereum",
        "composite_risk_score": 72,
        "risk_level": "high",
        "red_flags": ["HI01: Extreme holder concentration"],
        "module_risk_scores": {"holder_analysis": 90, "liquidity_verification": 40},
        "holder_analysis": {"hhi": 4200, "risk_level": "high", "total_holders": 1200},
        "liquidity_verification": {"total_locked_percentage": 45.0},
        "contract_authority": {"risk_level": "HIGH"},
        "dev_reputation": {"reputation_level": "suspicious", "reputation_score": 30},
        "contract_creation": {},
    }


def _sample_scan_report() -> dict:
    """Minimal v2 scan payload exercising the AEGIS forensics section."""
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
        "sentinel": {
            "free": {
                "holder_analysis": {"hhi": 4200, "risk_level": "high"},
                "liquidity_verification": {"total_locked_percentage": 45.0},
                "contract_authority": {"risk_level": "LOW"},
                "proxy_detection": {"is_proxy": False, "can_upgrade": False},
                "metadata_fingerprint": {},
                "dev_reputation": {"reputation_level": "safe", "reputation_score": 85},
            }
        },
        "top_holders": [],
        "socials": {},
        "threats": [],
    }


class TestRenderedBranding:
    def test_deepscan_section_renders_aegis_not_sentinel(self) -> None:
        out = format_sentinel_deepscan_section(_sample_deepscan())
        assert "🛡️ <b>AEGIS DEEP FORENSIC SCAN</b>" in out
        assert "SENTINEL" not in out

    def test_deepscan_footer_note_uses_aegis(self) -> None:
        out = format_sentinel_deepscan_section(_sample_deepscan())
        assert "AEGIS data is forensic evidence" in out

    def test_scan_report_forensics_header_renders_aegis(self) -> None:
        out = format_scan_report(_sample_scan_report())
        assert "🛡️ <b>AEGIS Forensics</b>" in out
        assert "SENTINEL" not in out

    def test_sentinel_flags_header_renders_aegis(self) -> None:
        out = format_sentinel_flags(_sample_scan_report())
        assert "🛡️ <b>AEGIS</b>" in out
        assert "SENTINEL" not in out

    def test_standalone_report_delegates_to_deepscan(self) -> None:
        out = format_sentinel_scan_report(_sample_deepscan())
        assert "AEGIS DEEP FORENSIC SCAN" in out
        assert "SENTINEL" not in out


class TestInternalIdentifiersUnchanged:
    def test_formatter_functions_still_importable(self) -> None:
        assert callable(format_sentinel_deepscan_section)
        assert callable(format_sentinel_flags)
        assert callable(format_sentinel_scan_report)

    def test_sentinel_constants_still_exist(self) -> None:
        assert isinstance(SENTINEL_MODULE_EMOJI, dict)
        assert "holder_analysis" in SENTINEL_MODULE_EMOJI
        assert isinstance(_SENTINEL_CODE_MAP, dict)
        assert "CR01" in _SENTINEL_CODE_MAP

    def test_module_emoji_keys_render_in_deepscan(self) -> None:
        out = format_sentinel_deepscan_section(_sample_deepscan())
        assert "Holder Analysis" in out
