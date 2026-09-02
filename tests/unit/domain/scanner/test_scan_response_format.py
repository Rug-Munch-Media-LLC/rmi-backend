"""Tests for the rich scan API response (``_format_scan_response``).

2026-08-25 quality push — the scan response must surface EVERY data point
the scanner already computes: holder concentration, top holders, bundler
counts, contract authority, wallet-metrics summary and chain-aware deep
links. Nothing may be fabricated — missing engine data stays None.
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.unified.model import ScanResult
from app.routers.unified_scanner_router import _deep_links, _format_scan_response


def _make_result(**overrides: Any) -> ScanResult:
    """ScanResult with rich engine payloads attached."""
    r = ScanResult(token_address="0xabc", chain="ethereum")
    r.name = "Test Token"
    r.symbol = "TEST"
    r.price_usd = 0.001
    r.market_cap_usd = 100_000.0
    r.liquidity_usd = 12_000.0
    r.volume_24h_usd = 50_000.0
    r.age_hours = 7.5
    r.safety_score = 42.0
    r.risk_level = "high"
    r.risk_flags = ["bundled_launch"]
    r.green_flags = ["lp_locked"]
    r.modules_run = ["wallet_metrics", "bundle_analysis", "risk_report"]
    r.wallet_metrics = {
        "result": "scanned",
        "holders_analyzed": 25,
        "fresh_wallet_pct": 11.0,
        "cex_funded_pct": 29.0,
        "first_tx_pct": 8.0,
    }
    r.bundle_analysis = {
        "is_bundled": True,
        "risk_label": "HIGH",
        "bundle_wallets": ["0x1", "0x2", "0x3"],
        "holder_count": 120,
    }
    r.first_buyers = {
        "buyers": [
            {"wallet": "0x1", "first_buy_ts": "2026-08-25T00:00:00Z", "bundled": True},
            {"wallet": "0x2", "first_buy_ts": "2026-08-25T00:00:01Z", "bundled": False},
        ],
        "total_buyers": 2,
    }
    r.sentinel = {
        "free": {
            "contract_authority": {
                "mint_authority": None,
                "freeze_authority": None,
                "renounced": True,
            }
        }
    }
    r.holder_concentration = {
        "top10_pct": 38.5,
        "top3_pct": 21.0,
        "gini": 0.72,
        "hhi": 1450.0,
        "class": "HIGH",
        "total_holders": 120,
        "clusters_found": 2,
        "top_holders": [
            {"address": "0xdeadbeef", "pct": 12.4, "label": "whale"},
        ],
    }
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


def test_format_includes_core_stats() -> None:
    """Core stats fields surface verbatim; absent data stays absent."""
    p = _format_scan_response(_make_result())
    assert p["price_usd"] == 0.001
    assert p["market_cap_usd"] == 100_000.0
    assert p["liquidity_usd"] == 12_000.0
    assert p["volume_24h_usd"] == 50_000.0
    assert p["age_hours"] == 7.5
    assert p["safety_score"] == 42.0
    assert p["risk_level"] == "high"
    assert p["risk_flags"] == ["bundled_launch"]
    assert p["green_flags"] == ["lp_locked"]
    assert p["breakdown"] is None  # absent data stays absent, never fabricated


def test_format_includes_holder_concentration_and_top_holders() -> None:
    """Holder concentration + top holders surface from the engine payload."""
    p = _format_scan_response(_make_result())
    hc = p["holder_concentration"]
    assert hc["top10_pct"] == 38.5
    assert hc["gini"] == 0.72
    assert hc["hhi"] == 1450.0
    assert hc["class"] == "HIGH"
    assert p["top_holders"][0]["address"] == "0xdeadbeef"
    assert p["top_holders"][0]["pct"] == 12.4
    assert p["holder_count"] == 120


def test_format_includes_bundler_and_first_buyers() -> None:
    """Bundler counts and first-buyer rows surface with bundled flags."""
    p = _format_scan_response(_make_result())
    assert p["bundler_count"] == 3
    assert p["bundled_first_buyers"] == 1
    assert p["bundler_pct"] == 50.0
    assert len(p["first_buyers"]["buyers"]) == 2


def test_format_includes_contract_authority_and_wallet_summary() -> None:
    """Contract authority + wallet-metrics summary surface."""
    p = _format_scan_response(_make_result())
    assert p["contract_authority"]["renounced"] is True
    wm = p["wallet_metrics_summary"]
    assert wm["fresh_wallet_pct"] == 11.0
    assert wm["cex_funded_pct"] == 29.0
    assert wm["first_tx_pct"] == 8.0
    assert wm["holders_analyzed"] == 25


def test_format_includes_chain_aware_deep_links() -> None:
    """Deep links are chain-aware (Etherscan/Solscan, Defined EVM-only)."""
    evm = _format_scan_response(_make_result())["deep_links"]
    assert "etherscan" in evm
    assert "solscan" not in evm
    assert evm["dexscreener"].endswith("/ethereum/0xabc")
    assert "gmgn" in evm
    assert "defined" in evm
    assert "geckoterminal" in evm

    sol = _format_scan_response(_make_result(chain="solana", token_address="So1mint"))["deep_links"]
    assert "solscan" in sol
    assert "etherscan" not in sol
    assert "defined" not in sol  # defined.fi is EVM-only


def test_format_degrades_gracefully_without_engine_data() -> None:
    """Missing engine data yields None fields, never fabricated values."""
    p = _format_scan_response(_make_result())
    p2 = _format_scan_response(
        _make_result(
            wallet_metrics=None,
            bundle_analysis=None,
            first_buyers=None,
            sentinel=None,
            holder_concentration=None,
        )
    )
    assert p2["holder_concentration"] is None
    assert p2["top_holders"] is None
    assert p2["bundler_count"] is None
    assert p2["bundled_first_buyers"] is None
    assert p2["bundler_pct"] is None
    assert p2["contract_authority"] is None
    assert p2["wallet_metrics_summary"] is None
    assert p2["deep_links"]["dexscreener"]  # links still render
    # sanity: rich result still parses
    assert p["status"] == "ok"


def test_deep_links_helper() -> None:
    """Edge cases: empty token → no links; empty chain still renders DexScreener."""
    assert _deep_links("", "ethereum") == {}
    assert _deep_links("0xabc", "")["dexscreener"].endswith("/0xabc")
