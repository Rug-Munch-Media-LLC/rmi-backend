"""Unit tests for the deep-scan report formatter additions.

Covers ``_scan_report_deepscan``:
  * ``format_deep_ml_scan_report`` — the 🟢/🔴 honeypot sell-sim verdict lines
    (and the "—" fail-open when unavailable);
  * ``format_sentinel_deepscan_section`` — the same-slot concentration line
    and the ``[REVIEW]`` note when Helius data is unavailable.

No network / Telegram calls.
"""

from __future__ import annotations

from typing import Any

from app.deep_scan import DeepScanReport
from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import (
    format_deep_ml_scan_report,
    format_sentinel_deepscan_section,
)

ADDR = "0x" + "11" * 20


def _ml_report(sell_sim: dict[str, Any], findings: list[dict[str, Any]] | None = None) -> DeepScanReport:
    return DeepScanReport(
        address=ADDR,
        chain="ethereum",
        risk_scores={
            "fraud_gnn": {"error": "stub"},
            "pyod": {"model_type": "skipped"},
            "pygod": {"model_type": "skipped"},
        },
        anomaly={},
        decompiled={"success": False},
        sell_sim=sell_sim,
        overall_risk="low",
        findings=findings or [],
    )


def test_ml_report_renders_sell_sim_ok_line() -> None:
    out = format_deep_ml_scan_report(
        _ml_report(
            {
                "sell_sim_verdict": "OK",
                "sell_tax": 2.0,
                "source": "honeypot.is",
                "simulation_success": True,
                "risk": "safe",
                "tax_pct": 0.02,
            }
        )
    )
    assert "🟢 Sell sim OK (tax 2.0%)" in out
    assert "(honeypot.is)" in out


def test_ml_report_renders_sell_sim_blocked_line() -> None:
    out = format_deep_ml_scan_report(
        _ml_report(
            {
                "sell_sim_verdict": "BLOCKED",
                "is_honeypot": True,
                "sell_tax": 100.0,
                "source": "goplus",
                "simulation_success": False,
            }
        )
    )
    assert "🔴 Sell sim BLOCKED / EXTREME taxes" in out
    assert "Sell sim OK" not in out


def test_ml_report_renders_sell_sim_extreme_taxes_line() -> None:
    out = format_deep_ml_scan_report(
        _ml_report(
            {
                "sell_sim_verdict": "EXTREME_TAXES",
                "sell_tax": 30.0,
                "source": "honeypot.is",
            }
        )
    )
    assert "🔴 Sell sim BLOCKED / EXTREME taxes" in out
    assert "30.0% sell tax" in out


def test_ml_report_fail_open_when_unavailable() -> None:
    out = format_deep_ml_scan_report(
        _ml_report(
            {
                "sell_sim_verdict": "unavailable",
                "sell_tax": None,
                "warnings": ["sell simulation data source unreachable"],
            }
        )
    )
    # Section renders honestly: no verdict line, but the warning is shown.
    assert "Sell Simulation" in out
    assert "Sell sim OK" not in out
    assert "BLOCKED" not in out
    assert "unreachable" in out


def test_ml_report_keeps_pyrevm_detail_line_when_both_run() -> None:
    out = format_deep_ml_scan_report(
        _ml_report(
            {
                "sell_sim_verdict": "OK",
                "sell_tax": 0.5,
                "source": "goplus",
                "simulation_success": True,
                "risk": "safe",
                "tax_pct": 0.005,
            }
        )
    )
    assert "🟢 Sell sim OK" in out
    assert "risk=safe" in out


# ── SENTINEL deepscan section: same-slot bundler ────────────────────────────


def _sentinel_data(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "address": ADDR,
        "chain": "solana",
        "composite_risk_score": 40,
        "risk_level": "medium",
        "red_flags": [],
        "module_risk_scores": {},
        "bundle_detection": bundle,
    }


def test_sentinel_renders_same_slot_concentration() -> None:
    bundle = {
        "same_block_snipers": [{"address": ADDR, "block_number": 100}],
        "deployer_funded": [],
        "risk_level": "HIGH",
        "total_bundled_percentage": 12.0,
        "same_slot": {
            "result": "scanned",
            "same_slot": 100,
            "same_slot_wallets": 5,
            "same_slot_sol_spent": 12.5,
            "same_slot_held_pct": 3.2,
        },
    }
    out = format_sentinel_deepscan_section(_sentinel_data(bundle))
    assert "Same-slot: <b>5</b> wallets" in out
    assert "12.50 SOL" in out
    assert "<b>3.2%</b> held" in out


def test_sentinel_renders_review_note_when_helius_unavailable() -> None:
    bundle = {
        "deployer_funded": [],
        "same_block_snipers": [],
        "risk_level": "LOW",
        "same_slot": {
            "result": "unavailable",
            "note": "[REVIEW] slot-level detail requires Helius data source (unavailable)",
            "is_bundled": True,
            "confidence": 72.0,
            "bundle_wallets": 9,
        },
    }
    out = format_sentinel_deepscan_section(_sentinel_data(bundle))
    assert "[REVIEW]" in out
    assert "Own bundle result: <b>9</b> wallets" in out
    assert "conf <b>72%</b>" in out


def test_sentinel_omits_same_slot_when_absent() -> None:
    out = format_sentinel_deepscan_section(_sentinel_data({"deployer_funded": [], "same_block_snipers": []}))
    assert "Same-slot" not in out
    assert "[REVIEW]" not in out
