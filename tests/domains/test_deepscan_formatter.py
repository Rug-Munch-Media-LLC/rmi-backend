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


def test_sentinel_renders_richer_bundle_verdict_line() -> None:
    """The 🔴 Bundle verdict (same-slot + 2-hop + holding/dumped + time nodes)."""
    out = format_sentinel_deepscan_section(
        _sentinel_cluster_data(
            {
                "check": "cluster_analysis",
                "result": "scanned",
                "cluster_line": "🕸️ 3 funders → 14 wallets · largest cluster 9 · coordinated",
                "bundle_line": "🔴 Bundle: 14 wallets · same-slot 9 · 2-hop funder chain · holding 62.0% (dumped 12.0%) · 3 time-nodes",
                "distinct_funders": 3,
                "funded_wallets": 14,
                "largest_cluster_size": 9,
                "clusters": [],
                "coordinated": True,
                "coordinated_buyers": 6,
                "funding_chain_buyers": 2,
                "verdict": "coordinated",
                "same_slot_buyers": 9,
                "funding_chain_depth": 2,
                "two_hop_chain_buyers": 7,
                "time_nodes": 3,
                "supply_grab": {"status": "holding", "held_pct": 62.0, "dumped_pct": 12.0, "exited_wallets": 1},
            }
        )
    )
    # Richer verdict line rendered after the classic cluster line.
    assert "Bundle: 14 wallets · same-slot 9 · 2-hop funder chain" in out
    assert "holding 62.0% (dumped 12.0%)" in out
    assert "3 time-nodes" in out
    # Detail lines for the deeper dimensions.
    assert "2-hop funding chain: <b>7</b> wallets" in out
    assert "Supply grab: <b>HOLDING</b>" in out
    assert "Time nodes (funded same hour): <b>3</b>" in out


def test_sentinel_renders_dumped_supply_grab_line() -> None:
    out = format_sentinel_deepscan_section(
        _sentinel_cluster_data(
            {
                "result": "scanned",
                "cluster_line": "🕸️ 1 funder → 2 wallets · largest cluster 2 · coordinated",
                "bundle_line": "🔴 Bundle: 2 wallets · 1-hop funder chain · holding 0.0% (dumped 100.0%)",
                "funding_chain_depth": 1,
                "supply_grab": {"status": "dumped", "held_pct": 0.0, "dumped_pct": 100.0, "exited_wallets": 2},
                "time_nodes": 0,
                "two_hop_chain_buyers": 0,
            }
        )
    )
    assert "Supply grab: <b>DUMPED</b>" in out
    assert "2 exited" in out
    assert "2-hop funding chain" not in out  # depth 1 → no 2-hop line
    assert "Time nodes" not in out  # 0 → omitted


def test_sentinel_omits_richer_lines_when_absent() -> None:
    """Backward compat: cluster data without the deeper fields renders as before."""
    out = format_sentinel_deepscan_section(
        _sentinel_cluster_data(
            {
                "result": "scanned",
                "cluster_line": "🕸️ 3 funders → 14 wallets · largest cluster 9 · coordinated",
                "coordinated_buyers": 6,
                "funding_chain_buyers": 2,
                "verdict": "coordinated",
            }
        )
    )
    assert "🕸️ <b>Cluster Analysis</b>" in out
    assert "Coordinated buys" in out
    assert "Funding-chain wallets" in out
    assert "Bundle:" not in out
    assert "Supply grab" not in out
    assert "Time nodes" not in out


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


def test_build_bundle_intel_includes_deeper_cluster_dimensions() -> None:
    from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import _build_bundle_intel

    data = _sentinel_bundle_data()
    data["cluster_analysis"] = {
        "result": "scanned",
        "distinct_funders": 3,
        "funded_wallets": 14,
        "largest_cluster_size": 9,
        "coordinated_buyers": 6,
        "funding_chain_buyers": 2,
        "verdict": "coordinated",
        "funding_chain_depth": 2,
        "two_hop_chain_buyers": 7,
        "time_nodes": 3,
        "supply_grab": {"status": "holding", "held_pct": 62.0, "dumped_pct": 12.0, "exited_wallets": 1},
    }
    intel = _build_bundle_intel(data)
    clusters = intel["funding_clusters"]
    assert clusters["funding_chain_depth"] == 2
    assert clusters["two_hop_chain_buyers"] == 7
    assert clusters["time_nodes"] == 3
    assert clusters["supply_grab"] == {"status": "holding", "held_pct": 62.0, "dumped_pct": 12.0}


def test_build_bundle_intel_omits_absent_cluster_dimensions() -> None:
    from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import _build_bundle_intel

    data = _sentinel_bundle_data()
    data["cluster_analysis"] = {
        "result": "scanned",
        "distinct_funders": 2,
        "funded_wallets": 3,
        "verdict": "organic",
        "funding_chain_depth": 0,
        "two_hop_chain_buyers": 0,
        "time_nodes": 0,
        "supply_grab": {"status": "none", "held_pct": 0.0, "dumped_pct": 0.0, "exited_wallets": 0},
    }
    intel = _build_bundle_intel(data)
    clusters = intel["funding_clusters"]
    assert clusters["distinct_funders"] == 2
    assert "funding_chain_depth" not in clusters  # 0 → omitted
    assert "two_hop_chain_buyers" not in clusters
    assert "time_nodes" not in clusters
    assert "supply_grab" not in clusters  # "none" → omitted, never fabricated


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


# ── First-20 Wallet Map: buyer-timing (fresh-wallet % / recently-funded %) ──


def _sentinel_timing_data() -> dict[str, Any]:
    """Wallet map with buyer_timing (own engine output)."""
    data = _sentinel_bundle_data()
    data["wallet_map"] = {
        "result": "scanned",
        "wallet_map_line": "🛠️1 🐳1 🍤8 🌱1 📦9",
        "roles": {"dev": 1, "whale": 1, "fish": 0, "shrimp": 8, "fresh": 1, "bundle": 9},
        "buyer_timing": {
            "result": "scanned",
            "fresh_wallet_pct": 62,
            "fresh_wallet_count": 8,
            "fresh_wallet_denominator": 13,
            "fresh_window_hours": 24,
            "recently_funded_pct": 71,
            "recently_funded_count": 10,
            "recently_funded_denominator": 14,
            "funded_window_hours": 6,
            "breakdown": [],
        },
        "fresh_wallet_line": "🌱 Fresh wallets: 62% (created <24h before buy)",
        "recently_funded_line": "💸 Recently funded: 71% (funded <6h before buy)",
    }
    data["cluster_analysis"] = {
        "check": "cluster_analysis",
        "result": "scanned",
        "cluster_line": "🕸️ 3 funders → 14 wallets · largest cluster 9 · coordinated",
        "distinct_funders": 3,
        "funded_wallets": 14,
        "largest_cluster_size": 9,
        "coordinated": True,
        "coordinated_buyers": 6,
        "funding_chain_buyers": 2,
        "verdict": "coordinated",
    }
    return data


def test_sentinel_renders_buyer_timing_lines_under_wallet_map() -> None:
    out = format_sentinel_deepscan_section(_sentinel_timing_data())
    assert "First-20 Wallet Map" in out
    # Rendered output is HTML-escaped (the engine line carries raw "<24h").
    assert "🌱 Fresh wallets: 62% (created &lt;24h before buy)" in out
    assert "💸 Recently funded: 71% (funded &lt;6h before buy)" in out


def test_sentinel_omits_buyer_timing_lines_when_absent() -> None:
    out = format_sentinel_deepscan_section(_sentinel_bundle_data())
    assert "Fresh wallets:" not in out
    assert "Recently funded:" not in out


def test_build_bundle_intel_includes_buyer_timing_and_cluster_signals() -> None:
    from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import _build_bundle_intel

    intel = _build_bundle_intel(_sentinel_timing_data())
    assert intel["buyer_timing"] == {
        "fresh_wallet_pct": 62,
        "fresh_window_hours": 24,
        "recently_funded_pct": 71,
        "funded_window_hours": 6,
    }
    assert intel["funding_clusters"] == {
        "distinct_funders": 3,
        "funded_wallets": 14,
        "largest_cluster_size": 9,
        "coordinated_buyers": 6,
        "funding_chain_buyers": 2,
        "cluster_verdict": "coordinated",
    }


def test_build_bundle_intel_omits_timing_when_unscanned() -> None:
    from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import _build_bundle_intel

    intel = _build_bundle_intel(_sentinel_bundle_data())
    assert "buyer_timing" not in intel
    assert "funding_clusters" not in intel


def test_render_analyst_read_receives_timing_and_cluster_intel() -> None:
    from unittest.mock import patch

    from app.domains.telegram.rugmunchbot.formatters._scan_report_deepscan import render_analyst_read_block

    captured: dict[str, Any] = {}

    async def _fake_chat(messages, tier, max_tokens=None):
        captured["messages"] = messages
        captured["tier"] = tier
        return "71% of buyers were freshly funded within 6h of launch — coordinated-fresh-wallet setup, not organic retail."

    async def _run() -> str:
        with patch("app.llm_config.chat_with_fallback", new=_fake_chat):
            return await render_analyst_read_block(_sentinel_timing_data())

    import asyncio

    block = asyncio.run(_run())
    assert "🧠 <b>Analyst Read</b>" in block
    assert "coordinated-fresh-wallet setup" in block
    # The prompt must carry the fresh/funded + cluster intel (snappy tier, 8s cap).
    assert captured["tier"] == "snappy"
    joined = str(captured["messages"])
    assert "fresh_wallet_pct" in joined
    assert "recently_funded_pct" in joined
    assert "funding_clusters" in joined
    assert "cluster_verdict" in joined


# ── SENTINEL deepscan section: Cross-Token Linkage (recurring cluster + serial launcher) ──


def test_sentinel_renders_recurring_cluster_line_in_cluster_section() -> None:
    data = _sentinel_cluster_data(
        {
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
    data["cross_token_link"] = {
        "result": "scanned",
        "recurring_cluster_line": "🕸️ Recurring cluster: 7 wallets also bought 3 prior launches",
        "recurring_wallets": 7,
        "prior_launch_count": 3,
        "prior_launch_tokens": [],
    }
    out = format_sentinel_deepscan_section(data)
    assert "Recurring cluster: 7 wallets also bought 3 prior launches" in out


def test_sentinel_renders_deployer_and_funder_link_lines() -> None:
    data = _sentinel_data({"deployer_funded": [], "same_block_snipers": []})
    data["cross_token_link"] = {
        "result": "scanned",
        "deployer_link_line": "🔗 Deployer linked to 4 prior launches: 0xabc…, 0xdef…",
        "deployer_prior_count": 4,
        "funder_link_line": "🔗 Funder also funded 2 other deployers",
        "funder_deployer_count": 2,
    }
    out = format_sentinel_deepscan_section(data)
    assert "🔗 <b>Cross-Token Linkage</b>" in out
    assert "Deployer linked to 4 prior launches: 0xabc…, 0xdef…" in out
    assert "Funder also funded 2 other deployers" in out


def test_sentinel_omits_cross_token_when_absent() -> None:
    out = format_sentinel_deepscan_section(_sentinel_data({"deployer_funded": [], "same_block_snipers": []}))
    assert "Cross-Token" not in out
    assert "Recurring cluster" not in out


def test_sentinel_renders_recurring_line_even_when_cluster_unavailable() -> None:
    data = _sentinel_cluster_data({"result": "unavailable", "cluster_line": "—", "verdict": "unknown"})
    data["cross_token_link"] = {
        "result": "scanned",
        "recurring_cluster_line": "🕸️ Recurring cluster: 5 wallets also bought 2 prior launches",
        "recurring_wallets": 5,
        "prior_launch_count": 2,
    }
    out = format_sentinel_deepscan_section(data)
    # Recurring signal survives even when the funding-source cluster analysis
    # itself is unavailable — the cross-token check runs on its own data.
    assert "Recurring cluster: 5 wallets also bought 2 prior launches" in out
