"""Error-path tests for ``handlers/investigate.py`` (``_determine_verdict``).

The verdict scorer is the first thing that touches a possibly-degraded scan
result. It must produce a verdict for every realistic failure shape: a scan
carrying ``risk_score=None`` / ``mcap=None`` / ``liquidity=None`` (provider
degradation), a bare ``{}`` scan, and a ``None`` wallet payload. Pure —
no network, no Telegram objects.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.handlers.investigate import _VERDICT_EMOJIS, _determine_verdict


class TestDetermineVerdictErrorPaths:
    def test_none_typed_scan_result_is_safe(self) -> None:
        """risk_score/mcap/liquidity None must not crash the verdict."""
        scan = {"risk_score": None, "mcap": None, "liquidity": None, "threats": None, "fresh_wallet_pct": None}
        verdict, desc = _determine_verdict(scan, {})
        assert verdict == "SAFE"
        assert "Clean" in desc
        assert verdict in _VERDICT_EMOJIS

    def test_empty_scan_result_is_safe(self) -> None:
        verdict, _ = _determine_verdict({}, {})
        assert verdict == "SAFE"

    def test_none_wallet_data_is_safe(self) -> None:
        scan = {"risk_score": 10}
        verdict, _ = _determine_verdict(scan, None)
        assert verdict == "SAFE"

    def test_none_typed_scan_with_missing_keys_is_safe(self) -> None:
        """A partially-degraded scan (only name/address survived) still gets
        a verdict — every lookup uses .get() with a default."""
        scan = {"name": "Degraded Token", "symbol": "DEG"}
        verdict, _ = _determine_verdict(scan, {})
        assert verdict == "SAFE"

    def test_honeypot_without_risk_still_critical(self) -> None:
        """A honeypot flag overrides a missing risk_score to CRITICAL."""
        scan = {"risk_score": None, "honeypot": True, "threats": ["HONEYPOT DETECTED"]}
        verdict, desc = _determine_verdict(scan, {})
        assert verdict == "CRITICAL"
        assert "1 threats" in desc

    def test_bundle_fresh_and_flags_escalate(self) -> None:
        scan = {
            "risk_score": 20,
            "bundle_detected": True,
            "fresh_wallet_pct": 60,
            "threats": ["bundle", "fresh"],
        }
        wallet = {"flags": ["a", "b", "c"]}
        verdict, _ = _determine_verdict(scan, wallet)
        # 20 + 15 (bundle) + 10 (fresh) + 10 (flags>2) = 55 → MEDIUM
        assert verdict == "MEDIUM"

    def test_low_liquidity_with_none_fields_never_escalates(self) -> None:
        """low_liq guard must not crash when liquidity/mcap are None."""
        scan = {"risk_score": 30, "liquidity": None, "mcap": None}
        verdict, _ = _determine_verdict(scan, {})
        assert verdict == "LOW"

    def test_score_never_exceeds_100(self) -> None:
        scan = {"risk_score": 95, "honeypot": True, "bundle_detected": True, "fresh_wallet_pct": 90}
        verdict, _ = _determine_verdict(scan, {"flags": ["a", "b", "c"]})
        assert verdict == "CRITICAL"

    def test_boundary_scores_map_to_bands(self) -> None:
        assert _determine_verdict({"risk_score": 80}, {})[0] == "CRITICAL"
        assert _determine_verdict({"risk_score": 60}, {})[0] == "HIGH"
        assert _determine_verdict({"risk_score": 40}, {})[0] == "MEDIUM"
        assert _determine_verdict({"risk_score": 20}, {})[0] == "LOW"
        assert _determine_verdict({"risk_score": 19}, {})[0] == "SAFE"
