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


# ── SENTINEL deepscan section: 🕸️ Cluster Analysis ─────────────────────────


def _sentinel_cluster_data(cluster: dict[str, Any]) -> dict[str, Any]:
    data = _sentinel_data({"deployer_funded": [], "same_block_snipers": []})
    data["cluster_analysis"] = cluster
    return data


def test_sentinel_renders_cluster_analysis_block() -> None:
    out = format_sentinel_deepscan_section(
        _sentinel_cluster_data(
            {
                "check": "cluster_analysis",
                "result": "scanned",
                "cluster_line": "🕸️ 3 funders → 14 wallets · largest cluster 9 · coordinated",
                "distinct_funders": 3,
                "funded_wallets": 14,
                "largest_cluster_size": 9,
                "clusters": [],
                "coordinated": True,
                "coordinated_buyers": 6,
                "funding_chain_buyers": 2,
                "verdict": "coordinated",
            }
        )
    )
    assert "🕸️ <b>Cluster Analysis</b>" in out
    assert "<code>🕸️ 3 funders → 14 wallets · largest cluster 9 · coordinated</code>" in out
    assert "Coordinated buys (same block + shared funder): <b>6</b>" in out
    assert "Funding-chain wallets (funder = launch buyer): <b>2</b>" in out


def test_sentinel_cluster_organic_verdict_omits_coordinated_line() -> None:
    out = format_sentinel_deepscan_section(
        _sentinel_cluster_data(
            {
                "result": "scanned",
                "cluster_line": "🕸️ 4 funders → 6 wallets · largest cluster 2 · organic",
                "distinct_funders": 4,
                "funded_wallets": 6,
                "largest_cluster_size": 2,
                "clusters": [],
                "coordinated": False,
                "coordinated_buyers": 0,
                "funding_chain_buyers": 0,
                "verdict": "organic",
            }
        )
    )
    assert "🕸️ <b>Cluster Analysis</b>" in out
    assert "· organic" in out
    assert "Coordinated buys" not in out
    assert "Funding-chain wallets" not in out


def test_sentinel_cluster_fail_open_when_unavailable() -> None:
    out = format_sentinel_deepscan_section(
        _sentinel_cluster_data(
            {
                "check": "cluster_analysis",
                "result": "unavailable",
                "cluster_line": "—",
                "verdict": "unknown",
                "note": "[REVIEW] funding-source clustering needs launch-buyer rows (no ClickHouse evm_transfers coverage for this token/chain)",
            }
        )
    )
    # Section renders honestly with the data-source gap note — never a fabric.
    assert "🕸️ <b>Cluster Analysis</b>" in out
    assert "[REVIEW]" in out
    assert "ClickHouse" in out


def test_sentinel_cluster_fail_open_plain_dash_without_note() -> None:
    out = format_sentinel_deepscan_section(_sentinel_cluster_data({"result": "unavailable", "cluster_line": "—"}))
    assert "🕸️ <b>Cluster Analysis</b>" in out
    assert "<code>—</code>" in out


def test_sentinel_omits_cluster_block_when_key_absent() -> None:
    out = format_sentinel_deepscan_section(_sentinel_data({"deployer_funded": [], "same_block_snipers": []}))
    assert "Cluster Analysis" not in out


# ── AI "Analyst Read" (bundle/cluster plain-language interpretation) ─────────


def _sentinel_bundle_data() -> dict[str, Any]:
    return {
        "address": ADDR,
        "chain": "solana",
        "composite_risk_score": 40,
        "risk_level": "medium",
        "red_flags": [],
        "module_risk_scores": {},
        "bundle_detection": {
            "deployer_funded": [{"address": "walletA"}],
            "same_block_snipers": [{"address": "walletB"}],
            "total_bundled_percentage": 62.0,
            "risk_level": "HIGH",
            "same_slot": {
                "result": "scanned",
                "same_slot": 999,
                "same_slot_wallets": 14,
                "same_slot_sol_spent": 43.21,
                "same_slot_held_pct": 62.0,
                "bundle_wallets": 9,
                "confidence": 88.0,
                "is_bundled": True,
            },
        },
        "wallet_map": {
            "result": "scanned",
            "wallet_map_line": "🛠️1 🐳1 🍤8 🌱1 📦9",
            "roles": {"dev": 1, "whale": 1, "fish": 0, "shrimp": 8, "fresh": 1, "bundle": 9},
        },
        "holder_analysis": {
            "hhi": 4200,
            "total_holders": 231,
            "top_10_percentage": 74.5,
            "top_20_percentage": 88.2,
            "new_wallets_percentage": 81.0,
        },
        "contract_creation": {"creator_address": ADDR, "deployer_funded_percentage": 55.0},
    }


def test_build_bundle_intel_gathers_compact_summary() -> None:
    from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import _build_bundle_intel

    intel = _build_bundle_intel(_sentinel_bundle_data())
    assert intel["bundle_wallet_count"] == 9
    assert intel["same_slot_wallets"] == 14
    assert intel["same_slot_sol_spent"] == 43.21
    assert intel["same_slot_held_pct"] == 62.0
    assert intel["wallet_roles"]["bundle"] == 9
    assert intel["deployer_funded_pct"] == 55.0
    assert intel["holder_concentration"]["top10_pct"] == 74.5


def test_build_bundle_intel_empty_when_no_bundle_data() -> None:
    from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import _build_bundle_intel

    assert _build_bundle_intel({"address": ADDR, "chain": "solana"}) == {}
    assert _build_bundle_intel({}) == {}


def test_render_analyst_read_block_returns_block_on_llm_answer() -> None:
    from unittest.mock import AsyncMock, patch

    from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import render_analyst_read_block

    async def _run() -> str:
        with patch(
            "app.llm_config.chat_with_fallback",
            new=AsyncMock(return_value="14 wallets same-slot, 62% held — coordinated launch, not organic demand"),
        ):
            return await render_analyst_read_block(_sentinel_bundle_data())

    import asyncio

    block = asyncio.run(_run())
    assert "🧠 <b>Analyst Read</b>" in block
    assert "coordinated launch" in block


def test_render_analyst_read_block_fail_open_empty() -> None:
    from unittest.mock import AsyncMock, patch

    from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import render_analyst_read_block

    async def _run() -> str:
        # Empty intel → no LLM call at all.
        with patch(
            "app.llm_config.chat_with_fallback",
            new=AsyncMock(side_effect=AssertionError("must not be called")),
        ):
            empty = await render_analyst_read_block({"address": ADDR, "chain": "solana"})
        # LLM failure → fail-open empty.
        with patch("app.llm_config.chat_with_fallback", new=AsyncMock(return_value="")):
            failed = await render_analyst_read_block(_sentinel_bundle_data())
        return empty + failed

    import asyncio

    assert asyncio.run(_run()) == ""


def test_sentinel_renders_analyst_read_before_risk_assessment() -> None:
    block = "🧠 <b>Analyst Read</b>\n14 wallets bought same-slot, holding 62%."
    out = format_sentinel_deepscan_section(_sentinel_bundle_data(), analyst_read=block)
    assert "Analyst Read" in out
    assert out.find("Analyst Read") < out.find("Risk Assessment")


def test_sentinel_skips_analyst_read_when_empty() -> None:
    out = format_sentinel_deepscan_section(_sentinel_bundle_data())
    assert "Analyst Read" not in out
    assert "Risk Assessment" in out


def test_ml_report_renders_analyst_read_before_key_metrics() -> None:
    block = "🧠 <b>Analyst Read</b>\nBundled launch, not organic."
    out = format_deep_ml_scan_report(_ml_report({"sell_sim_verdict": "OK", "sell_tax": 2.0}), analyst_read=block)
    assert "Analyst Read" in out
    assert out.find("Analyst Read") < out.find("Key Metrics")
